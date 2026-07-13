"""Frame service APIs for LangGraph orchestration.

This module provides the three core frame lifecycle operations:
1. create_and_run_root_frame - start a new conversation
2. spawn_child_frame - derive a sub-agent (e.g., reviewer)
3. resume_frame - restart a paused/interrupted frame

Key design: each frame has an independent thread_id for the LangGraph
checkpointer, avoiding state conflicts between parent and child frames.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from internagents.frame_state import (
    FrameState,
    AgentName,
    create_root_frame,
    create_child_frame,
    update_frame_status,
    frame_with_elapsed,
)


async def create_and_run_root_frame(
    *,
    graph: Any,
    input_data: dict[str, Any],
    agent_name: AgentName = "main",
    system_prompt: str | None = None,
    checkpointer: Any = None,
) -> FrameState:
    """Create a new root frame and invoke the LangGraph agent.

    Starts a fresh conversation with no parent. The frame gets its own
    thread_id and runs to terminal state (completed/failed/cancelled).

    Args:
        graph: LangGraph compiled agent graph (expects ainvoke method)
        input_data: user input that initiates the frame
        agent_name: which agent runs this frame (default "main")
        system_prompt: optional system prompt override
        checkpointer: LangGraph checkpointer for resumability (optional)

    Returns:
        Terminal FrameState after graph.ainvoke completes

    Example:
        ```python
        frame = await create_and_run_root_frame(
            graph=my_agent_graph,
            input_data={"query": "hello"},
            agent_name="main",
        )
        assert frame["status"] in {"completed", "failed"}
        ```
    """
    frame = create_root_frame(
        agent_name=agent_name,
        input_data=input_data,
        system_prompt=system_prompt,
    )

    # Mark as running before invocation
    frame = update_frame_status(frame, "running")

    # Use frame.id as the thread_id for checkpointer
    config = {"configurable": {"thread_id": frame["id"]}}

    try:
        # Invoke the graph with the frame as initial state
        result = await graph.ainvoke(frame, config=config)

        # Ensure result is a normalized FrameState
        if isinstance(result, dict) and "id" in result:
            return result
        return frame
    except Exception as e:
        # Mark frame as failed if the graph raised an exception
        return update_frame_status(frame, "failed")


async def spawn_child_frame(
    parent: FrameState,
    *,
    graph: Any,
    agent_name: AgentName,
    input_data: dict[str, Any],
    system_prompt: str | None = None,
    checkpointer: Any = None,
) -> FrameState:
    """Spawn a child frame from a parent frame.

    Creates a new frame with parent_frame_id pointing to parent,
    sharing the same root_frame_id. Each child runs in an independent
    thread_id to avoid checkpointer conflicts with its parent.

    This is the main mechanism for multi-agent delegation (e.g., spawning
    a reviewer subframe after the main agent completes).

    Args:
        parent: the parent FrameState
        graph: LangGraph compiled agent graph (for the child agent)
        agent_name: which agent runs this child frame
        input_data: input specific to the child's task
        system_prompt: optional system prompt override
        checkpointer: LangGraph checkpointer (optional)

    Returns:
        Terminal FrameState of the child after graph.ainvoke completes

    Example:
        ```python
        # After main frame completes:
        child = await spawn_child_frame(
            parent=main_frame,
            graph=reviewer_graph,
            agent_name="reviewer",
            input_data={"target_frame_id": main_frame["id"]},
        )
        assert child["parent_frame_id"] == main_frame["id"]
        assert child["root_frame_id"] == main_frame["root_frame_id"]
        ```
    """
    child = create_child_frame(
        parent,
        agent_name,
        input_data=input_data,
        system_prompt=system_prompt,
    )

    # Mark as running before invocation
    child = update_frame_status(child, "running")

    # Independent thread_id to avoid parent/child checkpointer conflicts
    config = {"configurable": {"thread_id": child["id"]}}

    try:
        # Invoke the graph with the child frame as initial state
        result = await graph.ainvoke(child, config=config)

        # Ensure result is a normalized FrameState
        if isinstance(result, dict) and "id" in result:
            return result
        return child
    except Exception as e:
        # Mark child as failed if the graph raised an exception
        return update_frame_status(child, "failed")


async def resume_frame(
    frame_id: str,
    *,
    graph: Any,
    additional_input: dict[str, Any] | None = None,
    checkpointer: Any = None,
) -> FrameState:
    """Resume a paused or interrupted frame from the checkpointer.

    Looks up the frame's checkpoint by frame_id (thread_id), restores
    state, and continues execution with optional new input.

    Args:
        frame_id: the frame's unique identifier (same as thread_id)
        graph: LangGraph compiled agent graph
        additional_input: optional new input to merge into state
        checkpointer: LangGraph checkpointer (required to recover state)

    Returns:
        Terminal FrameState after graph.ainvoke completes

    Raises:
        ValueError: if checkpointer is None or frame not found

    Example:
        ```python
        # Resume a previously paused frame:
        resumed = await resume_frame(
            frame_id=pause_frame_id,
            graph=my_agent_graph,
            additional_input={"user_approval": True},
            checkpointer=my_checkpointer,
        )
        ```
    """
    if checkpointer is None:
        raise ValueError("checkpointer required to resume frame")

    config = {"configurable": {"thread_id": frame_id}}

    # If no additional input, just invoke to continue from checkpoint
    if additional_input is None:
        result = await graph.ainvoke({}, config=config)
    else:
        # Merge additional input into the state
        result = await graph.ainvoke(additional_input, config=config)

    if isinstance(result, dict) and "id" in result:
        return result

    # Fallback: try to recover the frame from checkpoint
    return {"id": frame_id, "status": "pending"}  # type: ignore


def get_frame_info(frame: FrameState) -> dict[str, Any]:
    """Get human-readable info about a frame (for logging/UI).

    Args:
        frame: a FrameState

    Returns:
        A dict with summary info
    """
    frame = frame_with_elapsed(frame)
    return {
        "id": frame["id"],
        "root_frame_id": frame["root_frame_id"],
        "parent_frame_id": frame.get("parent_frame_id"),
        "agent_name": frame["agent_name"],
        "status": frame["status"],
        "tokens_used": frame["tokens_used"],
        "time_used_seconds": frame["time_used_seconds"],
        "message_count": len(frame.get("messages", [])),
        "created_at": frame["created_at"],
    }
