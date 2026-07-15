"""LangChain tools that let the agent query and manage the persistent thread frame.

A Frame represents the active execution unit for a thread. Tools:
- get_frame: read current frame state (objective, status, budget, elapsed)
- update_frame: mark the current frame `completed` or `blocked`
- spawn_subframe: delegate an independent sub-task to a child frame
"""

from __future__ import annotations

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
    """Build a Command that writes frame_* fields to state and emits a tool message.

    Fields written match `InternAgentState` frame_* fields (fixed by R1).
    """
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


@tool("spawn_subframe")
async def spawn_subframe(
    agent_name: Literal["main"],
    objective: str,
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """Delegate an independent sub-task to a child work-frame and wait for its result.

    The child inherits the current root_frame_id but runs in its own thread with
    a fresh objective. This blocks until the child reaches a terminal status
    (completed/failed/cancelled/blocked); its output_data is returned so the
    caller can use the result.

    Use when: the work is genuinely separable from your main line of reasoning
    (an independent literature search, a self-contained computation) and would
    otherwise clutter your working context. Do NOT use for tool wrappers or
    single-shot lookups — the frame overhead only pays off for multi-step work.
    """
    current = _current_frame(runtime)
    if current is None:
        return {"error": "cannot spawn subframe because this thread has no parent frame", "frame": None}

    if agent_name != "main":
        return {
            "error": f"agent_name={agent_name!r} not allowed; only 'main' is spawnable via this tool",
            "frame": None,
        }

    from internagents.frame_service import spawn_child_frame

    try:
        child = await spawn_child_frame(
            current,
            agent_name=agent_name,
            input_data={"objective": objective},
        )
    except Exception as exc:  # noqa: BLE001
        from internagents.frame_middleware import _dbg
        _dbg(f"Tool · SPAWN failed: {exc}")
        return {"error": f"spawn_subframe failed: {exc}", "frame": None}

    from internagents.frame_middleware import _dbg
    _dbg(
        f"Tool · SPAWN parent={current['id'][:8]} child={child['id'][:8]} "
        f"agent={agent_name} status={child.get('status')}"
    )

    return {
        "child_frame_id": child["id"],
        "root_frame_id": child.get("root_frame_id"),
        "status": child.get("status"),
        "output_data": child.get("output_data") or {},
    }


def frame_tools() -> list[Any]:
    return [get_frame, update_frame, spawn_subframe]
