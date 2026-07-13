"""Frame execution model for InternAgentS multi-agent orchestration.

A Frame is the atomic unit of agent execution. All agent runs (main, reviewer, etc.)
are represented as Frame instances organized in a tree structure:
- root_frame_id: identifies the session boundary (one user conversation = one root)
- parent_frame_id: tracks agent hierarchy (reviewer is a child of main)
- agent_name: which agent is executing this frame
- status: execution phase (pending/running/completed/failed/cancelled)

Key design:
- Each Frame has a unique thread_id for LangGraph checkpointing (no shared state with siblings)
- Messages and context_data form the complete resumable snapshot
- Skills/MCP attachments are tracked per-frame for future evolution features
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal, NotRequired, TypedDict

from langchain_core.messages import AnyMessage

from internagents.goal_state import GoalState


FrameStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
AgentName = Literal["main", "reviewer", "bookmarker", "onboarding"]


class FrameValidationError(ValueError):
    """Raised when a frame request is invalid."""


class FrameState(TypedDict):
    """Complete snapshot of one agent execution unit.

    Attributes:
        id: unique frame identifier (UUID)
        root_frame_id: session identifier (same for all frames in one conversation tree)
        parent_frame_id: which frame spawned this one (None for root)
        agent_name: which agent is executing ("main", "reviewer", etc.)
        status: execution phase
        messages: LangGraph message array (AnyMessage)
        system_prompt: assembled system prompt for this frame's execution
        input_data: raw input that triggered this frame
        output_data: final output/result from this frame
        tokens_used: cumulative token consumption
        time_used_seconds: elapsed execution time
        created_at: unix timestamp when created
        updated_at: unix timestamp of last state change
        skills_attached: names of attached skills (for evolution tracking)
        mcp_servers_attached: names of attached MCP servers
        evolution_context: reserved for skill-evolution module (Phase 3+)
    """

    id: str
    root_frame_id: str
    parent_frame_id: NotRequired[str | None]
    agent_name: AgentName
    status: FrameStatus
    messages: list[AnyMessage]
    system_prompt: NotRequired[str]
    input_data: NotRequired[dict[str, Any]]
    output_data: NotRequired[dict[str, Any]]
    tokens_used: int
    time_used_seconds: int
    created_at: int
    updated_at: int
    skills_attached: NotRequired[list[str]]
    mcp_servers_attached: NotRequired[list[str]]
    evolution_context: NotRequired[dict[str, Any]]


def unix_seconds() -> int:
    """Return current unix timestamp in seconds."""
    return int(time.time())


def validate_frame_id(frame_id: str) -> str:
    """Validate and return a frame ID."""
    normalized = str(frame_id).strip()
    if not normalized:
        raise FrameValidationError("frame id must not be empty")
    return normalized


def validate_agent_name(agent_name: AgentName) -> AgentName:
    """Validate and return an agent name."""
    valid_names: set[AgentName] = {"main", "reviewer", "bookmarker", "onboarding"}
    if agent_name not in valid_names:
        raise FrameValidationError(
            f"agent_name must be one of {valid_names}, got {agent_name!r}"
        )
    return agent_name


def validate_frame_status(status: FrameStatus) -> FrameStatus:
    """Validate and return a frame status."""
    valid_statuses: set[FrameStatus] = {"pending", "running", "completed", "failed", "cancelled"}
    if status not in valid_statuses:
        raise FrameValidationError(
            f"status must be one of {valid_statuses}, got {status!r}"
        )
    return status


def create_root_frame(
    *,
    agent_name: AgentName = "main",
    input_data: dict[str, Any] | None = None,
    system_prompt: str | None = None,
    frame_id: str | None = None,
    now: int | None = None,
) -> FrameState:
    """Create a new root frame (no parent, root_frame_id = id).

    Args:
        agent_name: which agent will execute this frame (default "main")
        input_data: user input that triggered this frame
        system_prompt: optional system prompt to use
        frame_id: optional explicit frame ID (for LangGraph thread pinning). If None, generates a UUID.
        now: override current timestamp (for testing)

    Returns:
        A new pending root FrameState

    Raises:
        FrameValidationError: if agent_name or frame_id is invalid
    """
    agent_name = validate_agent_name(agent_name)
    timestamp = unix_seconds() if now is None else now
    if frame_id is None:
        frame_id = str(uuid.uuid4())
    else:
        frame_id = validate_frame_id(frame_id)

    frame: FrameState = {
        "id": frame_id,
        "root_frame_id": frame_id,
        "parent_frame_id": None,
        "agent_name": agent_name,
        "status": "pending",
        "messages": [],
        "tokens_used": 0,
        "time_used_seconds": 0,
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    if input_data is not None:
        frame["input_data"] = dict(input_data)

    if system_prompt is not None:
        frame["system_prompt"] = system_prompt

    return frame


def create_child_frame(
    parent: FrameState,
    agent_name: AgentName,
    *,
    input_data: dict[str, Any] | None = None,
    system_prompt: str | None = None,
    now: int | None = None,
) -> FrameState:
    """Create a new child frame derived from a parent.

    The child inherits the root_frame_id from its parent, creating a tree structure.
    Each child has its own frame_id and thread_id for independent execution.

    Args:
        parent: the parent FrameState
        agent_name: which agent will execute this child frame
        input_data: input specific to this child
        system_prompt: optional system prompt override
        now: override current timestamp (for testing)

    Returns:
        A new pending child FrameState

    Raises:
        FrameValidationError: if agent_name is invalid
    """
    agent_name = validate_agent_name(agent_name)
    timestamp = unix_seconds() if now is None else now
    frame_id = str(uuid.uuid4())

    frame: FrameState = {
        "id": frame_id,
        "root_frame_id": parent["root_frame_id"],
        "parent_frame_id": parent["id"],
        "agent_name": agent_name,
        "status": "pending",
        "messages": [],
        "tokens_used": 0,
        "time_used_seconds": 0,
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    if input_data is not None:
        frame["input_data"] = dict(input_data)

    if system_prompt is not None:
        frame["system_prompt"] = system_prompt

    return frame


def normalize_frame_state(value: Any) -> FrameState | None:
    """Attempt to normalize/validate a value as a FrameState.

    Returns None if value is not a valid frame or dict-like.
    Used for defensive state recovery and type checking.

    Args:
        value: a potential FrameState

    Returns:
        Normalized FrameState or None
    """
    if not isinstance(value, dict):
        return None

    frame_id = value.get("id")
    root_frame_id = value.get("root_frame_id")
    agent_name = value.get("agent_name")
    status = value.get("status")

    if not isinstance(frame_id, str) or not frame_id.strip():
        return None
    if not isinstance(root_frame_id, str) or not root_frame_id.strip():
        return None

    try:
        agent_name = validate_agent_name(agent_name)
        status = validate_frame_status(status)
    except FrameValidationError:
        return None

    timestamp = unix_seconds()
    created_at = _int_or_default(value.get("created_at"), timestamp)
    messages = value.get("messages")
    if not isinstance(messages, list):
        messages = []

    frame: FrameState = {
        "id": frame_id,
        "root_frame_id": root_frame_id,
        "parent_frame_id": value.get("parent_frame_id")
        if isinstance(value.get("parent_frame_id"), str)
        else None,
        "agent_name": agent_name,
        "status": status,
        "messages": messages,
        "tokens_used": max(0, _int_or_default(value.get("tokens_used"), 0)),
        "time_used_seconds": max(0, _int_or_default(value.get("time_used_seconds"), 0)),
        "created_at": created_at,
        "updated_at": _int_or_default(value.get("updated_at"), created_at),
    }

    if isinstance(value.get("system_prompt"), str):
        frame["system_prompt"] = value["system_prompt"]

    if isinstance(value.get("input_data"), dict):
        frame["input_data"] = value["input_data"]

    if isinstance(value.get("output_data"), dict):
        frame["output_data"] = value["output_data"]

    if isinstance(value.get("skills_attached"), list):
        frame["skills_attached"] = value["skills_attached"]

    if isinstance(value.get("mcp_servers_attached"), list):
        frame["mcp_servers_attached"] = value["mcp_servers_attached"]

    if isinstance(value.get("evolution_context"), dict):
        frame["evolution_context"] = value["evolution_context"]

    return frame


def frame_with_elapsed(frame: FrameState, *, now: int | None = None) -> FrameState:
    """Return a frame with updated time_used_seconds based on elapsed time.

    If the frame is still running, recalculates time_used_seconds.
    If terminal (completed/failed/cancelled), returns unchanged.

    Args:
        frame: the FrameState to update
        now: override current timestamp (for testing)

    Returns:
        Updated FrameState (original unchanged)
    """
    if frame.get("status") in {"completed", "failed", "cancelled"}:
        return frame

    timestamp = unix_seconds() if now is None else now
    updated: FrameState = dict(frame)  # type: ignore
    updated["time_used_seconds"] = max(
        updated.get("time_used_seconds", 0),
        timestamp - updated.get("created_at", timestamp),
    )
    return updated


def update_frame_status(
    frame: FrameState,
    status: FrameStatus,
    *,
    now: int | None = None,
) -> FrameState:
    """Update a frame's status and metadata.

    Args:
        frame: the FrameState to update
        status: new status
        now: override current timestamp (for testing)

    Returns:
        Updated FrameState (original unchanged)

    Raises:
        FrameValidationError: if status is invalid
    """
    status = validate_frame_status(status)
    timestamp = unix_seconds() if now is None else now

    updated: FrameState = dict(frame)  # type: ignore
    updated["status"] = status
    updated["updated_at"] = timestamp
    updated["time_used_seconds"] = max(
        updated.get("time_used_seconds", 0),
        timestamp - updated.get("created_at", timestamp),
    )
    return updated


def frame_from_goal(goal: GoalState) -> FrameState:
    """Convert a legacy GoalState to a FrameState for backward compatibility.

    Maps GoalState.objective → FrameState.input_data["objective"]
    Maps GoalState.tokenBudget → carried in evolution_context

    Args:
        goal: a GoalState TypedDict

    Returns:
        A new FrameState with equivalent data
    """
    frame_id = goal.get("id", str(uuid.uuid4()))
    now = unix_seconds()

    frame: FrameState = {
        "id": frame_id,
        "root_frame_id": goal.get("threadId") or frame_id,
        "parent_frame_id": None,
        "agent_name": "main",
        "status": "pending",  # type: ignore
        "messages": [],
        "tokens_used": goal.get("tokensUsed", 0),
        "time_used_seconds": goal.get("timeUsedSeconds", 0),
        "created_at": goal.get("createdAt", now),
        "updated_at": goal.get("updatedAt", now),
    }

    if goal.get("objective"):
        frame["input_data"] = {"objective": goal["objective"]}

    if goal.get("tokenBudget"):
        frame["evolution_context"] = {"legacy_token_budget": goal["tokenBudget"]}

    return frame


def goal_from_frame(frame: FrameState) -> GoalState:
    """Convert a FrameState back to a GoalState for backward compatibility.

    Used by goal_middleware and existing code that expects GoalState.

    Args:
        frame: a FrameState TypedDict

    Returns:
        A new GoalState with equivalent data
    """
    objective = ""
    if isinstance(frame.get("input_data"), dict):
        objective = frame["input_data"].get("objective", "")

    goal: GoalState = {
        "id": frame["id"],
        "objective": objective or frame.get("system_prompt", ""),
        "status": "active",  # type: ignore
        "tokensUsed": frame.get("tokens_used", 0),
        "timeUsedSeconds": frame.get("time_used_seconds", 0),
        "createdAt": frame.get("created_at", unix_seconds()),
        "updatedAt": frame.get("updated_at", unix_seconds()),
    }

    if frame.get("root_frame_id"):
        goal["threadId"] = frame["root_frame_id"]

    if isinstance(frame.get("evolution_context"), dict):
        budget = frame["evolution_context"].get("legacy_token_budget")
        if isinstance(budget, int) and budget > 0:
            goal["tokenBudget"] = budget

    return goal


def _int_or_default(value: Any, default: int) -> int:
    """Try to coerce a value to int, return default on failure."""
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
