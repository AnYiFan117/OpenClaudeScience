"""Frame execution model for 天玄·千枢科学发现平台 multi-agent orchestration.

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


FrameStatus = Literal["pending", "running", "completed", "failed", "cancelled", "blocked"]
AgentName = Literal["main", "reviewer", "bookmarker", "onboarding"]
TERMINAL_FRAME_STATUSES: set[FrameStatus] = {"completed", "failed", "cancelled", "blocked"}
ACTIVE_FRAME_STATUSES: set[FrameStatus] = {"pending", "running"}
MAX_FRAME_OBJECTIVE_CHARS = 4_000


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
    valid_statuses: set[FrameStatus] = {"pending", "running", "completed", "failed", "cancelled", "blocked"}
    if status not in valid_statuses:
        raise FrameValidationError(
            f"status must be one of {valid_statuses}, got {status!r}"
        )
    return status


def validate_frame_objective(objective: str) -> str:
    """Validate and normalize a frame objective string."""
    normalized = str(objective).strip()
    if not normalized:
        raise FrameValidationError("frame objective must not be empty")
    if len(normalized) > MAX_FRAME_OBJECTIVE_CHARS:
        raise FrameValidationError(
            f"frame objective must be at most {MAX_FRAME_OBJECTIVE_CHARS} characters"
        )
    return normalized


def validate_token_budget(token_budget: int | None) -> int | None:
    """Validate an optional positive-integer token budget."""
    if token_budget is None:
        return None
    if not isinstance(token_budget, int) or token_budget <= 0:
        raise FrameValidationError("token budget must be a positive integer")
    return token_budget


def create_root_frame(
    *,
    agent_name: AgentName = "main",
    input_data: dict[str, Any] | None = None,
    system_prompt: str | None = None,
    frame_id: str | None = None,
    token_budget: int | None = None,
    now: int | None = None,
) -> FrameState:
    """Create a new root frame (no parent, root_frame_id = id).

    Args:
        agent_name: which agent will execute this frame (default "main")
        input_data: user input that triggered this frame
        system_prompt: optional system prompt to use
        frame_id: optional explicit frame ID (for LangGraph thread pinning). If None, generates a UUID.
        token_budget: optional positive-integer token budget (stored in evolution_context["token_budget"])
        now: override current timestamp (for testing)

    Returns:
        A new pending root FrameState

    Raises:
        FrameValidationError: if agent_name, frame_id, or token_budget is invalid
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

    budget = validate_token_budget(token_budget)
    if budget is not None:
        frame["evolution_context"] = {"token_budget": budget}

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
    If terminal (completed/failed/cancelled/blocked), returns unchanged.

    Args:
        frame: the FrameState to update
        now: override current timestamp (for testing)

    Returns:
        Updated FrameState (original unchanged)
    """
    if frame.get("status") in TERMINAL_FRAME_STATUSES:
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


def frame_response(frame: FrameState | None, *, now: int | None = None) -> dict[str, Any]:
    """Render a Frame as a tool-response payload.

    Includes elapsed time and remaining token budget (if any) for the model to reason about.

    Args:
        frame: the FrameState or None
        now: override current timestamp (for testing)

    Returns:
        dict with keys "frame" and "remainingTokens"
    """
    elapsed_frame = frame_with_elapsed(frame, now=now) if frame else None
    remaining_tokens = None
    if elapsed_frame:
        budget = _extract_token_budget(elapsed_frame)
        if isinstance(budget, int):
            remaining_tokens = max(0, budget - elapsed_frame.get("tokens_used", 0))
    return {
        "frame": elapsed_frame,
        "remainingTokens": remaining_tokens,
    }


def _extract_token_budget(frame: FrameState) -> int | None:
    """Read token_budget from evolution_context if present."""
    ctx = frame.get("evolution_context")
    if isinstance(ctx, dict):
        budget = ctx.get("token_budget")
        if isinstance(budget, int) and budget > 0:
            return budget
    return None


def _int_or_default(value: Any, default: int) -> int:
    """Try to coerce a value to int, return default on failure."""
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
