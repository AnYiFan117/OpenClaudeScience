"""LangChain tools that let the agent query and manage the persistent thread frame.

A Frame represents the active execution unit for a thread. Tools:
- get_frame: read current frame state (objective, status, budget, elapsed)
- update_frame: mark the current frame `completed` or `blocked`
- delegate_subframes: batch delegate independent sub-tasks with optional fire-and-forget
- collect_subframes: collect results for wait=False children
- stop_subframes: cancel running children
- list_child_frames: inspect currently running child frames
- send_frame_message: send message to parent or child frames
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from internagents.frame_state import (
    ACTIVE_FRAME_STATUSES,
    FrameState,
    FrameValidationError,
    create_root_frame,
    frame_response,
    update_frame_status,
)


def _thread_id(runtime: ToolRuntime) -> str | None:
    execution_info = getattr(runtime, "execution_info", None)
    thread_id = getattr(execution_info, "thread_id", None)
    if thread_id:
        return str(thread_id)
    configurable = (runtime.config or {}).get("configurable", {})
    fallback = configurable.get("thread_id") or configurable.get("threadId")
    return str(fallback) if fallback else None


def _current_frame(runtime: ToolRuntime) -> FrameState | None:
    """Reconstruct the current FrameState from top-level state fields."""
    state = runtime.state or {}
    if not isinstance(state, dict):
        return None
    frame_id = state.get("frame_id")
    if not isinstance(frame_id, str) or not frame_id:
        return None
    from internagents.frame_state import normalize_frame_state
    frame_dict: dict[str, Any] = {
        "id": frame_id,
        "root_frame_id": state.get("root_frame_id") or frame_id,
        "parent_frame_id": state.get("parent_frame_id"),
        "agent_name": state.get("agent_name", "main"),
        "status": state.get("frame_status", "running"),
        "messages": [],
        "tokens_used": state.get("tokens_used", 0),
        "time_used_seconds": state.get("time_used_seconds", 0),
        "created_at": state.get("created_at", 0),
        "updated_at": state.get("updated_at", 0),
    }
    if isinstance(state.get("input_data"), dict):
        frame_dict["input_data"] = state["input_data"]
    if isinstance(state.get("output_data"), dict):
        frame_dict["output_data"] = state["output_data"]
    if isinstance(state.get("evolution_context"), dict):
        frame_dict["evolution_context"] = state["evolution_context"]
    return normalize_frame_state(frame_dict)


def _tool_message(runtime: ToolRuntime, payload: dict[str, Any]) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        tool_call_id=runtime.tool_call_id or "frame-tool",
    )


def _command_with_frame(runtime: ToolRuntime, frame: FrameState) -> Command:
    """Build a Command that writes frame_* fields to state and emits a tool message."""
    payload = frame_response(frame)
    update_dict: dict[str, Any] = {
        "frame_id": frame["id"],
        "root_frame_id": frame["root_frame_id"],
        "parent_frame_id": frame.get("parent_frame_id"),
        "agent_name": frame["agent_name"],
        "frame_status": frame["status"],
        "tokens_used": frame.get("tokens_used", 0),
        "time_used_seconds": frame.get("time_used_seconds", 0),
        "messages": [_tool_message(runtime, payload)],
    }
    if frame.get("input_data") is not None:
        update_dict["input_data"] = frame["input_data"]
    if frame.get("output_data") is not None:
        update_dict["output_data"] = frame["output_data"]
    if frame.get("evolution_context") is not None:
        update_dict["evolution_context"] = frame["evolution_context"]
    return Command(update=update_dict)


@tool("get_frame")
def get_frame(runtime: ToolRuntime) -> dict[str, Any]:
    """Get the current thread frame, including status, objective, budget, elapsed time, and remaining tokens."""
    return frame_response(_current_frame(runtime))


@tool("update_frame")
def update_frame(
    status: Literal["completed", "blocked"],
    runtime: ToolRuntime,
) -> Command | dict[str, Any]:
    """Mark the current frame completed or blocked.

    Set completed only after the objective is achieved and verified. Set blocked only when
    meaningful progress cannot continue without user input or an external-state change.
    """
    current = _current_frame(runtime)
    if current is None:
        return {"error": "cannot update frame because this thread has no frame", "frame": None}

    try:
        updated = update_frame_status(current, status)
    except FrameValidationError as exc:
        return {"error": str(exc), **frame_response(current)}

    from internagents.frame_middleware import _dbg
    _dbg(
        f"Tool · UPDATE frame_id={current['id'][:8]} "
        f"{current['status']} → {status}"
    )

    return _command_with_frame(runtime, updated)


@tool("delegate_subframes")
async def delegate_subframes(
    requests: list[dict[str, Any]],
    wait: bool = True,
    timeout: float | None = None,
    runtime: ToolRuntime = None,
) -> dict[str, Any]:
    """Batch delegate up to 48 independent sub-tasks with optional fire-and-forget.

    wait=True (default): spawn all concurrently, block until all finish, return terminal results.
    wait=False: dispatch all without blocking, return running descriptors with frame_ids.
    timeout: if wait=True and set, gather with a deadline; unfinished slots return running descriptors.

    Each request in the list must have "objective" or "task" field (and optionally "name", "agent_name").
    Agent defaults to "main".
    """
    current = _current_frame(runtime)
    if current is None:
        return {"error": "cannot delegate subframes: this thread has no frame", "frames": None}

    # Validate: max 48 requests
    if len(requests) > 48:
        return {"error": f"max 48 requests per batch, got {len(requests)}", "frames": None}

    from internagents.frame_service import (
        dispatch_child_frame_nowait,
        collect_child_frames,
    )

    results: list[dict[str, Any]] = []

    # wait=False: fire-and-forget dispatch
    if not wait:
        dispatched_ids = []
        for req in requests:
            objective = req.get("objective") or req.get("task") or ""
            agent_name = req.get("agent_name", "main")
            name = req.get("name")

            if not objective:
                results.append({"error": "objective/task required", "frame": None})
                continue

            if agent_name not in {"main", "reviewer"}:
                results.append({"error": f"agent_name={agent_name!r} not supported", "frame": None})
                continue

            try:
                desc = await dispatch_child_frame_nowait(
                    current,
                    agent_name=agent_name,
                    input_data={"objective": objective},
                    name=name,
                )
                results.append(desc)
                dispatched_ids.append(desc["frame_id"])
            except Exception as exc:  # noqa: BLE001
                from internagents.frame_middleware import _dbg
                _dbg(f"Tool · DELEGATE dispatch failed: {exc}")
                results.append({"error": f"dispatch failed: {exc}", "frame": None})

        from internagents.frame_middleware import _dbg
        _dbg(f"Tool · DELEGATE dispatched {len(dispatched_ids)} frames (wait=False)")
        return {"dispatched": True, "frames": results, "frame_ids": dispatched_ids}

    # wait=True: dispatch all, then collect with optional timeout
    dispatched_ids = []
    for req in requests:
        objective = req.get("objective") or req.get("task") or ""
        agent_name = req.get("agent_name", "main")
        name = req.get("name")

        if not objective:
            results.append({"error": "objective/task required", "frame": None})
            continue

        if agent_name not in {"main", "reviewer"}:
            results.append({"error": f"agent_name={agent_name!r} not supported", "frame": None})
            continue

        try:
            desc = await dispatch_child_frame_nowait(
                current,
                agent_name=agent_name,
                input_data={"objective": objective},
                name=name,
            )
            dispatched_ids.append(desc["frame_id"])
        except Exception as exc:  # noqa: BLE001
            from internagents.frame_middleware import _dbg
            _dbg(f"Tool · DELEGATE dispatch failed: {exc}")
            results.append({"error": f"dispatch failed: {exc}", "frame": None})

    # Collect all dispatched children with optional timeout
    if dispatched_ids:
        try:
            collected = await collect_child_frames(dispatched_ids, timeout=timeout or 30.0)
            for collected_result in collected:
                if collected_result.get("frame_id") in dispatched_ids:
                    results.append({
                        "child_frame_id": collected_result["frame_id"],
                        "root_frame_id": collected_result.get("root_frame_id"),
                        "status": collected_result.get("status"),
                        "output_data": collected_result.get("output_data") or {},
                    })
        except Exception as exc:  # noqa: BLE001
            from internagents.frame_middleware import _dbg
            _dbg(f"Tool · DELEGATE collect failed: {exc}")
            # Mark all as errored
            for fid in dispatched_ids:
                results.append({"error": f"collect failed: {exc}", "frame": None})

    from internagents.frame_middleware import _dbg
    _dbg(f"Tool · DELEGATE gathered {len([r for r in results if 'child_frame_id' in r])} frames (wait=True)")
    return {"wait": True, "frames": results}


@tool("collect_subframes")
async def collect_subframes(
    frame_ids: list[str],
    timeout: float = 30.0,
    runtime: ToolRuntime = None,
) -> dict[str, Any]:
    """Collect results for previously dispatched wait=False children.

    Bounded blocking wait (default 30s); unfinished slots return {frame_id, status:"running"}.
    """
    if not frame_ids:
        return {"error": "frame_ids list cannot be empty", "frames": None}

    from internagents.frame_service import collect_child_frames

    try:
        results = await collect_child_frames(frame_ids, timeout=timeout)
    except ValueError as exc:
        return {"error": str(exc), "frames": None}
    except Exception as exc:  # noqa: BLE001
        from internagents.frame_middleware import _dbg
        _dbg(f"Tool · COLLECT failed: {exc}")
        return {"error": f"collect_subframes failed: {exc}", "frames": None}

    from internagents.frame_middleware import _dbg
    terminal_count = sum(1 for r in results if r.get("status") in {"completed", "failed", "cancelled"})
    _dbg(f"Tool · COLLECT {terminal_count}/{len(results)} frames terminal (timeout={timeout}s)")

    return {"frames": results, "timeout": timeout}


@tool("stop_subframes")
async def stop_subframes(
    frame_ids: list[str],
    reason: str | None = None,
    runtime: ToolRuntime = None,
) -> dict[str, Any]:
    """Stop running child frames; returns their persisted state."""
    if not frame_ids:
        return {"error": "frame_ids list cannot be empty", "frames": None}

    from internagents.frame_service import stop_child_frame

    results = []
    for fid in frame_ids:
        try:
            state = await stop_child_frame(fid, reason=reason)
            results.append(state)
        except Exception as exc:  # noqa: BLE001
            from internagents.frame_middleware import _dbg
            _dbg(f"Tool · STOP failed for {fid}: {exc}")
            results.append({"frame_id": fid, "error": str(exc)})

    from internagents.frame_middleware import _dbg
    _dbg(f"Tool · STOP cancelled {len(frame_ids)} frames" + (f" (reason: {reason})" if reason else ""))

    return {"frames": results, "reason": reason}


@tool("list_child_frames")
async def list_child_frames(
    only_running: bool = True,
    runtime: ToolRuntime = None,
) -> dict[str, Any]:
    """List child frames dispatched from the current parent frame.

    Returns {running_children: [{frame_id, agent_name, status, started_at?}], count}
    only_running=True (default): only frames still executing (status="running").
    only_running=False: also include terminal children whose results have not been collected.
    """
    current = _current_frame(runtime)
    if current is None:
        return {"error": "no current frame", "running_children": [], "count": 0}
    from internagents.frame_service import list_child_frames_by_parent
    children = list_child_frames_by_parent(current["id"], only_running=only_running)
    return {"running_children": children, "count": len(children)}


@tool("send_frame_message")
async def send_frame_message(
    target: str,
    message: str,
    kind: Literal["info", "question"] = "info",
    runtime: ToolRuntime = None,
) -> dict[str, Any]:
    """Message a direct parent or direct child frame.

    target: either a child frame_id, or literal "parent".
    Topology: only direct parent + direct children. Siblings/grandparents refused.
    Returns {status: "sent"|"injected"|"refused", detail: ...}
    """
    current = _current_frame(runtime)
    if current is None:
        return {"status": "refused", "error": "no current frame"}
    from internagents.frame_service import send_message_to_frame
    return await send_message_to_frame(current, target, message, kind)


@tool("wait_for_notification")
async def wait_for_notification(
    timeout_seconds: int = 60,
    runtime: ToolRuntime = None,
) -> dict[str, Any]:
    """CS host wait_for_notification analog: park until a child frame lands,
    completes, or sends a message. Blocks up to `timeout_seconds` (max 1800).

    Returns:
      {status: "received", notifications: [{notification_type, sender_frame_id, payload, created_at}, ...]}
        — one or more events; drain all of them before calling again.
      {status: "timeout", notifications: [], pending_work: {children: N, ...}}
        — deadline hit and children still running.
      {status: "error"} — nothing to wait for (empty queue AND no running children).

    Notification types:
      - "child_landed"  : a wait=False child was just dispatched (payload={frame_id, name, agent_name})
      - "completion"    : a child reached terminal state (payload=terminal descriptor)
      - "message"       : another frame sent us a send_frame_message (payload={message, kind})

    Use in the CS canonical idle pattern: dispatch children with wait=False,
    then loop `wait_for_notification` with a modest timeout, acting on each
    landed event and repeating until status=error means the fan-out is done.
    """
    current = _current_frame(runtime)
    if current is None:
        return {"status": "error", "reason": "no current frame"}
    from internagents.frame_service import wait_for_frame_notifications
    if timeout_seconds is None:
        timeout_seconds = 60
    if timeout_seconds > 1800:
        timeout_seconds = 1800
    if timeout_seconds < 0:
        timeout_seconds = 0
    return await wait_for_frame_notifications(current["id"], timeout_seconds=timeout_seconds)


def frame_tools() -> list[Any]:
    return [
        get_frame,
        update_frame,
        delegate_subframes,
        collect_subframes,
        stop_subframes,
        list_child_frames,
        send_frame_message,
        wait_for_notification,
    ]

