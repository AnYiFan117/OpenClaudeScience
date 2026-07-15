"""Bridge LangGraph's cancel API to pregel's cooperative drain mechanism.

Root cause of "stop doesn't work": LangGraph 0.11's cancel HTTP handler sets a
per-run `done` event that the streaming layer polls, but it does NOT call
`RunControl.request_drain()`. Pregel's main loop checks `drain_requested` at
each superstep boundary — without the drain flag set, the graph keeps
executing model+tool loops until it naturally exits.

Fix: monkey-patch `langgraph_runtime_inmem.ops.listen_for_cancellation` so that
after it sets `done`, we ALSO call `request_drain()` on the run's RunControl.
Pregel picks it up at the next superstep and gracefully halts. The middleware
maintains a `run_id → RunControl` registry so the patch can find the right one.

The patched behaviour, end to end:
  UI Stop → POST /runs/{rid}/cancel → done.set(UserInterrupt())
    → [our patch] RunControl.request_drain(reason="user_cancel")
    → pregel loop notices at next superstep → status="draining" → GraphDrained
    → agent stops within one turn (current tool/model call still finishes).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from weakref import WeakValueDictionary

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langgraph.config import get_config
from langgraph.errors import GraphBubbleUp
from langgraph.runtime import RunControl

_logger = logging.getLogger(__name__)

# run_id → RunControl (weak refs so we don't hold state across runs)
_RUN_CONTROLS: "WeakValueDictionary[str, RunControl]" = WeakValueDictionary()


def _install_cancel_patch() -> None:
    """Wire listen_for_cancellation → RunControl.request_drain().

    Idempotent: if already patched (module reload), skips. Silently no-ops if
    the inmem backend is not installed (e.g. production Postgres backend).
    """
    try:
        from langgraph_runtime_inmem import ops as _ops
    except ImportError:
        return

    if getattr(_ops, "_cancel_bridge_installed", False):
        return

    original = _ops.listen_for_cancellation

    async def patched(control_queue, run_id, thread_id, done):
        result = await original(control_queue, run_id, thread_id, done)
        try:
            ctrl = _RUN_CONTROLS.get(str(run_id))
            if ctrl is not None and not ctrl.drain_requested:
                reason = "user_cancel" if getattr(done, "_value", None) else "shutdown"
                ctrl.request_drain(reason=reason)
                _logger.info(
                    f"[Cancel] drained RunControl run_id={run_id} reason={reason}"
                )
        except Exception as exc:  # noqa: BLE001
            _logger.debug(f"[Cancel] drain propagation failed: {exc}")
        return result

    _ops.listen_for_cancellation = patched
    _ops._cancel_bridge_installed = True
    _logger.info(
        "[Cancel] listen_for_cancellation patched — cancel HTTP now drains RunControl"
    )


_install_cancel_patch()


class _CancelDrained(GraphBubbleUp):
    """Raised inside a middleware when drain was requested before this model call."""

    def __init__(self, reason: str = "user_cancel") -> None:
        self.reason = reason
        super().__init__(f"Graph drained (short-circuit): {reason}")


@dataclass
class CancelBridgeMiddleware(AgentMiddleware):
    """Register RunControl for cancel-drain bridging + short-circuit if drained.

    Registers each run's RunControl in a module-level weak dict so the
    monkey-patched `listen_for_cancellation` can find and drain it. Also
    checks `drain_requested` at every model call so a drained run stops
    immediately instead of waiting for the next pregel superstep boundary
    (in practice the two happen at the same time, but this makes it explicit).
    """

    @property
    def name(self) -> str:
        return "CancelBridgeMiddleware"

    def _register(self, request: ModelRequest) -> None:
        rt = getattr(request, "runtime", None)
        if rt is None or getattr(rt, "control", None) is None:
            return
        try:
            cfg = get_config() or {}
            run_id = cfg.get("configurable", {}).get("run_id")
            if run_id:
                _RUN_CONTROLS[str(run_id)] = rt.control
        except Exception:  # noqa: BLE001
            pass

    def _drain_check(self, request: ModelRequest) -> None:
        rt = getattr(request, "runtime", None)
        if rt is None:
            return
        if getattr(rt, "drain_requested", False):
            reason = "user_cancel"
            ctrl = getattr(rt, "control", None)
            if ctrl is not None and ctrl.drain_reason:
                reason = ctrl.drain_reason
            _logger.info(f"[Cancel] short-circuiting model call, reason={reason}")
            raise _CancelDrained(reason=reason)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        self._register(request)
        self._drain_check(request)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        self._register(request)
        self._drain_check(request)
        return await handler(request)
