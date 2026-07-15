"""Inject the workspace's persisted user memory into every main-agent turn.

Onboarding writes a short markdown summary of the user (via `write_memory`
tool) to `.internagents/user-memory/<workspace_id>.md`. This middleware
reads that file at every model call for the main agent and appends its
contents to the system message so cross-thread continuity works: any new
thread in the workspace starts with the user's context already in view.

Best-effort: file missing or unreadable → no-op, main agent runs as usual.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from internagents.internagent_resources import ResourceConfig

_logger = logging.getLogger(__name__)


def _append_to_system_message(
    system_message: SystemMessage | None,
    text: str,
) -> SystemMessage:
    """Mirror of frame_middleware._append_to_system_message."""
    new_content: list[dict[str, Any]] = (
        list(system_message.content_blocks) if system_message else []
    )
    if new_content:
        text = f"\n\n{text}"
    new_content.append({"type": "text", "text": text})
    return SystemMessage(content_blocks=new_content)


def _render_user_memory(content: str) -> str:
    """Wrap the raw memory content so the LLM knows what it's reading."""
    return (
        "## User memory (from onboarding)\n\n"
        "Durable notes about this user, collected during first-run onboarding. "
        "Use them to tailor responses. Do not repeat them back verbatim.\n\n"
        f"{content.strip()}"
    )


@dataclass
class UserMemoryMiddleware(AgentMiddleware):
    """Read and inject the workspace's user-memory file each turn."""

    resource: ResourceConfig

    @property
    def name(self) -> str:
        return f"UserMemoryMiddleware_{self.resource.id}"

    def _load_memory(self) -> str | None:
        try:
            # Deferred to avoid circular imports at module load time.
            from internagents.frame_service import _workspace_id_from_resource

            workspace_id = _workspace_id_from_resource(self.resource.id)
        except Exception as e:  # noqa: BLE001
            _logger.debug(f"UserMemory: workspace_id lookup failed: {e}")
            return None

        try:
            path = (
                Path.cwd() / ".internagents" / "user-memory" / f"{workspace_id}.md"
            )
            if not path.exists():
                return None
            content = path.read_text(encoding="utf-8").strip()
            return content or None
        except Exception as e:  # noqa: BLE001
            _logger.debug(f"UserMemory: read failed: {e}")
            return None

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        memory = self._load_memory()
        if memory:
            request = request.override(
                system_message=_append_to_system_message(
                    request.system_message,
                    _render_user_memory(memory),
                )
            )
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        memory = self._load_memory()
        if memory:
            request = request.override(
                system_message=_append_to_system_message(
                    request.system_message,
                    _render_user_memory(memory),
                )
            )
        return await handler(request)
