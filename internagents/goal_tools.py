"""LangChain tools that let the agent manage persistent thread goals.

Internally uses Frame as source of truth; Goal is derived as a backward-compat view
via goal_from_frame for compatibility with legacy code.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from internagents.frame_state import create_root_frame, goal_from_frame, update_frame_status
from internagents.goal_state import (
    GoalState,
    GoalValidationError,
    goal_response,
    normalize_goal_state,
    update_goal_status,
)


def _thread_id(runtime: ToolRuntime) -> str | None:
    execution_info = getattr(runtime, "execution_info", None)
    thread_id = getattr(execution_info, "thread_id", None)
    if thread_id:
        return str(thread_id)
    configurable = (runtime.config or {}).get("configurable", {})
    fallback = configurable.get("thread_id") or configurable.get("threadId")
    return str(fallback) if fallback else None


def _current_goal(runtime: ToolRuntime) -> GoalState | None:
    return normalize_goal_state((runtime.state or {}).get("goal"))


def _tool_message(runtime: ToolRuntime, payload: dict[str, Any]) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        tool_call_id=runtime.tool_call_id or "goal-tool",
    )


def _command_with_frame(runtime: ToolRuntime, goal: GoalState, frame: dict[str, Any] | None = None) -> Command:
    """Create a Command to update state with frame and goal (derived view).

    Since Frame is the source of truth and Goal is derived for backward compatibility,
    this function writes Frame fields to state and derives the Goal view from it.

    Args:
        runtime: the ToolRuntime from LangChain
        goal: the new GoalState (derived from frame)
        frame: optional FrameState to also update in state

    Returns:
        A Command with goal (and frame if provided) updates
    """
    payload = goal_response(goal)
    update_dict = {"goal": goal, "messages": [_tool_message(runtime, payload)]}

    # If a frame is provided, also update frame fields in state
    if frame:
        update_dict["frame_id"] = frame.get("id")
        update_dict["root_frame_id"] = frame.get("root_frame_id")
        update_dict["parent_frame_id"] = frame.get("parent_frame_id")
        update_dict["agent_name"] = frame.get("agent_name")
        update_dict["frame_status"] = frame.get("status")
        update_dict["tokens_used"] = frame.get("tokens_used", 0)
        update_dict["time_used_seconds"] = frame.get("time_used_seconds", 0)

    return Command(update=update_dict)


@tool("get_goal")
def get_goal(runtime: ToolRuntime) -> dict[str, Any]:
    """Get the current thread goal, including status, budget, elapsed time, and remaining tokens."""

    return goal_response(_current_goal(runtime))


@tool("create_goal")
def create_goal(
    objective: str,
    runtime: ToolRuntime,
    token_budget: int | None = None,
) -> Command | dict[str, Any]:
    """Create a new active goal only when the user explicitly asks for persistent goal mode.

    Use token_budget only when the user explicitly provides a positive token budget.
    This fails when this thread already has an active goal; terminal goals can be replaced
    by a new active goal.

    Internally creates a Frame first, then derives a Goal view from it for backward compatibility.
    """

    current = _current_goal(runtime)
    if current is not None and current.get("status") == "active":
        return {
            "error": "cannot create a new goal because this thread already has an active goal",
            **goal_response(current),
        }

    try:
        # Create frame first with input data
        frame = create_root_frame(
            agent_name="main",
            input_data={"objective": objective, "token_budget": token_budget} if token_budget else {"objective": objective},
            frame_id=_thread_id(runtime),  # pin frame_id to thread_id if available
        )

        # Derive goal from frame
        goal = goal_from_frame(frame)

        # Preserve token budget in goal
        if token_budget is not None:
            goal["tokenBudget"] = token_budget

    except GoalValidationError as exc:
        return {"error": str(exc), "goal": None, "remainingTokens": None}

    return _command_with_frame(runtime, goal, frame=frame)


@tool("update_goal")
def update_goal(
    status: Literal["complete", "blocked"],
    runtime: ToolRuntime,
) -> Command | dict[str, Any]:
    """Mark the current goal complete or blocked.

    Set complete only after the objective is achieved and verified. Set blocked only when meaningful
    progress cannot continue without user input or an external-state change.

    Also updates the corresponding Frame status if present in state.
    """

    current = _current_goal(runtime)
    if current is None:
        return {"error": "cannot update goal because this thread has no goal", "goal": None}

    try:
        goal = update_goal_status(current, status)
    except GoalValidationError as exc:
        return {"error": str(exc), **goal_response(current)}

    # Map goal status to frame status
    frame_status_map = {"complete": "completed", "blocked": "blocked"}
    frame_status = frame_status_map.get(status, "running")

    # Create a synthetic frame update if frame_id exists in state
    frame_update = None
    if hasattr(runtime, "state") and isinstance(runtime.state, dict) and runtime.state.get("frame_id"):
        from internagents.frame_state import FrameState
        synthetic_frame: FrameState = {
            "id": runtime.state.get("frame_id", current["id"]),
            "root_frame_id": runtime.state.get("root_frame_id", current.get("threadId", current["id"])),
            "agent_name": runtime.state.get("agent_name", "main"),
            "status": frame_status,
            "messages": [],
            "tokens_used": runtime.state.get("tokens_used", 0),
            "time_used_seconds": runtime.state.get("time_used_seconds", 0),
            "created_at": current.get("createdAt", 0),
            "updated_at": current.get("updatedAt", 0),
        }
        frame_update = synthetic_frame

    return _command_with_frame(runtime, goal, frame=frame_update)


def goal_tools() -> list[Any]:
    return [get_goal, create_goal, update_goal]
