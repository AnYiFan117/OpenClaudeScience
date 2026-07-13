"""LangChain tools that let the agent manage the persistent thread frame.

A Frame represents the active execution unit for a thread. Tools:
- get_frame: read current frame state (objective, status, budget, elapsed)
- create_frame: create a new active frame with an objective (only when user explicitly asks for frame mode)
- update_frame: mark the current frame `completed` or `blocked`
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


@tool("create_frame")
def create_frame(
    objective: str,
    runtime: ToolRuntime,
    token_budget: int | None = None,
) -> Command | dict[str, Any]:
    """Create a new active frame only when the user explicitly asks for persistent frame mode.

    Use token_budget only when the user explicitly provides a positive token budget.
    This fails when this thread already has an active frame; terminal frames can be replaced
    by a new active frame.
    """
    current = _current_frame(runtime)
    if current is not None and current.get("status") in ACTIVE_FRAME_STATUSES:
        return {
            "error": "cannot create a new frame because this thread already has an active frame",
            **frame_response(current),
        }

    try:
        frame = create_root_frame(
            agent_name="main",
            input_data={"objective": objective},
            frame_id=_thread_id(runtime),
            token_budget=token_budget,
        )
    except FrameValidationError as exc:
        return {"error": str(exc), "frame": None, "remainingTokens": None}

    # Newly-created frame becomes active immediately (running)
    frame = update_frame_status(frame, "running")
    return _command_with_frame(runtime, frame)


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

    return _command_with_frame(runtime, updated)


def frame_tools() -> list[Any]:
    return [get_frame, create_frame, update_frame]
