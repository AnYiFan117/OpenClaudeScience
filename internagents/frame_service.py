"""Frame service APIs for LangGraph orchestration.

This module provides the three core frame lifecycle operations:
1. create_and_run_root_frame - start a new conversation
2. spawn_child_frame - derive a sub-agent (e.g., reviewer)
3. resume_frame - restart a paused/interrupted frame
4. spawn_bookmarker_background_task - concurrent bookmarker loop (v0.2)

Key design: each frame has an independent thread_id for the LangGraph
checkpointer, avoiding state conflicts between parent and child frames.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from internagents.frame_state import (
    FrameState,
    AgentName,
    create_root_frame,
    create_child_frame,
    update_frame_status,
    frame_with_elapsed,
)

_logger = logging.getLogger(__name__)


async def create_and_run_root_frame(
    *,
    input_data: dict[str, Any],
    agent_name: AgentName = "main",
    system_prompt: str | None = None,
    checkpointer: Any = None,
    graph: Any | None = None,
) -> FrameState:
    """Create a new root frame and invoke the LangGraph agent.

    Starts a fresh conversation with no parent. The frame gets its own
    thread_id and runs to terminal state (completed/failed/cancelled).

    Args:
        input_data: user input that initiates the frame
        agent_name: which agent runs this frame (default "main")
        system_prompt: optional system prompt override
        checkpointer: LangGraph checkpointer for resumability (optional)
        graph: optional LangGraph compiled agent graph. If None, resolves via get_agent_graph(...)

    Returns:
        Terminal FrameState after graph.ainvoke completes

    Example:
        ```python
        frame = await create_and_run_root_frame(
            input_data={"query": "hello"},
            agent_name="main",
        )
        assert frame["status"] in {"completed", "failed"}
        ```
    """
    # Resolve graph if not provided
    if graph is None:
        from internagents.agent_graph import get_agent_graph
        graph = get_agent_graph("local", agent_name)

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
        _logger.exception(f"create_and_run_root_frame failed: {e}")
        return update_frame_status(frame, "failed")


async def spawn_child_frame(
    parent: FrameState,
    *,
    agent_name: AgentName,
    input_data: dict[str, Any],
    system_prompt: str | None = None,
    checkpointer: Any = None,
    graph: Any | None = None,
) -> FrameState:
    """Spawn a child frame from a parent frame.

    Creates a new frame with parent_frame_id pointing to parent,
    sharing the same root_frame_id. Each child runs in an independent
    thread_id to avoid checkpointer conflicts with its parent.

    This is the main mechanism for multi-agent delegation (e.g., spawning
    a reviewer subframe after the main agent completes).

    Args:
        parent: the parent FrameState
        agent_name: which agent runs this child frame
        input_data: input specific to the child's task
        system_prompt: optional system prompt override
        checkpointer: LangGraph checkpointer (optional)
        graph: optional LangGraph compiled agent graph. If None, resolves via get_agent_graph(...)

    Returns:
        Terminal FrameState of the child after graph.ainvoke completes

    Example:
        ```python
        # After main frame completes:
        child = await spawn_child_frame(
            parent=main_frame,
            agent_name="reviewer",
            input_data={"target_frame_id": main_frame["id"]},
        )
        assert child["parent_frame_id"] == main_frame["id"]
        assert child["root_frame_id"] == main_frame["root_frame_id"]
        ```
    """
    # Resolve graph if not provided
    if graph is None:
        from internagents.agent_graph import get_agent_graph
        graph = get_agent_graph("local", agent_name)

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
        _logger.exception(f"spawn_child_frame failed: {e}")
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


async def spawn_reviewer(
    parent: FrameState,
    *,
    review_target: dict[str, Any] | None = None,
    checkpointer: Any = None,
    graph: Any | None = None,
) -> FrameState:
    """Spawn a reviewer child frame targeting the parent's output.

    Convenience wrapper around spawn_child_frame that pre-configures
    the reviewer agent with the parent's output as the review target.

    Args:
        parent: the parent FrameState (usually main agent)
        review_target: optional review target data (defaults to parent's output)
        checkpointer: LangGraph checkpointer (optional)
        graph: optional LangGraph compiled reviewer agent graph. If None, resolves via get_agent_graph(...)

    Returns:
        Terminal FrameState of the reviewer after completion

    Example:
        ```python
        main_frame = await create_and_run_root_frame(...)
        review_frame = await spawn_reviewer(
            parent=main_frame,
        )
        assert review_frame["parent_frame_id"] == main_frame["id"]
        ```
    """
    from internagents.agent_registry import get_agent_config

    _ = get_agent_config("reviewer")  # Sanity check that reviewer is registered

    # Resolve graph if not provided
    if graph is None:
        from internagents.agent_graph import get_agent_graph
        graph = get_agent_graph("local", "reviewer")

    input_data = {
        "review_target": review_target or parent.get("output_data") or {},
    }

    return await spawn_child_frame(
        parent,
        agent_name="reviewer",
        input_data=input_data,
        checkpointer=checkpointer,
        graph=graph,
    )


async def spawn_onboarding(
    *,
    user_id: str | None = None,
    checkpointer: Any = None,
    graph: Any | None = None,
) -> FrameState:
    """Run a one-shot onboarding frame for a new user.

    Creates a root frame (not a child) with the onboarding agent,
    runs it to completion.

    Args:
        user_id: optional user identifier for context
        checkpointer: LangGraph checkpointer (optional)
        graph: optional LangGraph compiled onboarding agent graph. If None, resolves via get_agent_graph(...)

    Returns:
        Terminal FrameState of the onboarding session

    Example:
        ```python
        onboarding_frame = await spawn_onboarding(
            user_id="user@example.com",
        )
        assert onboarding_frame["agent_name"] == "onboarding"
        ```
    """
    from internagents.agent_registry import get_agent_config

    _ = get_agent_config("onboarding")  # Sanity check

    # Resolve graph if not provided
    if graph is None:
        from internagents.agent_graph import get_agent_graph
        graph = get_agent_graph("local", "onboarding")

    return await create_and_run_root_frame(
        input_data={"user_id": user_id or "default"},
        agent_name="onboarding",
        checkpointer=checkpointer,
        graph=graph,
    )


async def _read_frame_checkpoint(
    frame_id: str,
    checkpointer: Any,
) -> dict[str, Any] | None:
    """Read the latest checkpoint for a frame from the checkpointer.

    Gracefully handles missing checkpointer, missing frame, or unknown checkpointer API.

    Args:
        frame_id: the frame's unique identifier (thread_id)
        checkpointer: LangGraph checkpointer, or None

    Returns:
        State dict from checkpoint, or None if unavailable
    """
    if checkpointer is None:
        return None

    config = {"configurable": {"thread_id": frame_id}}

    try:
        # Try standard LangGraph checkpointer API: aget(config)
        # Returns tuple (values, metadata) or similar
        result = await checkpointer.aget(config)
        if result is None:
            return None

        # Handle tuple return (values, metadata)
        if isinstance(result, tuple) and len(result) >= 1:
            state = result[0]
            if isinstance(state, dict):
                return state

        # Handle direct dict return
        if isinstance(result, dict):
            return result

        return None
    except (AttributeError, TypeError, Exception) as e:
        _logger.debug(f"checkpointer.aget failed for frame {frame_id}: {e}")
        return None


def _extract_bookmarks(graph_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract bookmarks from a bookmarker graph result.

    Tries multiple strategies:
    1. Parse last assistant message as JSON
    2. Extract from output_data["bookmarks"]
    3. Split by lines if output_data["result"] looks like a list

    Args:
        graph_result: result dict from graph.ainvoke

    Returns:
        List of bookmark dicts, or empty list if none found
    """
    bookmarks = []

    # Try output_data["bookmarks"]
    output_data = graph_result.get("output_data", {})
    if isinstance(output_data, dict):
        if "bookmarks" in output_data:
            bm = output_data["bookmarks"]
            if isinstance(bm, list):
                bookmarks.extend(bm)
            elif isinstance(bm, str):
                try:
                    bookmarks.extend(json.loads(bm))
                except (json.JSONDecodeError, TypeError):
                    pass

    # Try output_data["result"] as JSON
    if not bookmarks and "result" in output_data:
        result_str = str(output_data["result"])
        try:
            data = json.loads(result_str)
            if isinstance(data, list):
                bookmarks.extend(data)
            elif isinstance(data, dict) and "bookmarks" in data:
                bms = data["bookmarks"]
                if isinstance(bms, list):
                    bookmarks.extend(bms)
        except (json.JSONDecodeError, TypeError):
            pass

    # Try last message as JSON
    if not bookmarks:
        messages = graph_result.get("messages", [])
        if messages:
            last_msg = messages[-1]
            # Try to extract content as JSON
            content = getattr(last_msg, 'content', None) or ""
            try:
                # Look for JSON array in content
                import re
                match = re.search(r'\[\s*\{.*?\}\s*\]', str(content), re.DOTALL)
                if match:
                    bookmarks.extend(json.loads(match.group()))
            except (json.JSONDecodeError, AttributeError, TypeError):
                pass

    return bookmarks


async def _bookmarker_loop(
    parent_frame_id: str,
    root_frame_id: str,
    interval_seconds: int,
    bookmarks_dir: Path,
    checkpointer: Any,
) -> None:
    """Main loop for concurrent bookmarker task.

    Periodically reads parent frame state from checkpoint, invokes bookmarker
    on new messages, and writes bookmarks to JSONL output file.

    Args:
        parent_frame_id: ID of the frame to monitor
        root_frame_id: Root session ID for output file naming
        interval_seconds: sleep interval between checks
        bookmarks_dir: directory to write bookmark JSONL files
        checkpointer: LangGraph checkpointer
    """
    from internagents.agent_graph import get_agent_graph

    last_message_count = 0
    bookmarks_dir = bookmarks_dir or Path.home() / ".internagents" / "bookmarks"
    bookmarks_dir.mkdir(parents=True, exist_ok=True)
    output_file = bookmarks_dir / f"{root_frame_id}.jsonl"
    bookmarker_graph = get_agent_graph("local", "bookmarker")

    try:
        while True:
            await asyncio.sleep(interval_seconds)

            # Read latest state from checkpointer
            snapshot = await _read_frame_checkpoint(parent_frame_id, checkpointer)
            if snapshot is None:
                continue

            messages = snapshot.get("messages", [])
            if len(messages) <= last_message_count:
                continue  # No new messages

            # Prepare bookmarker input: recent messages since last check
            new_slice = messages[last_message_count:]
            input_data = {
                "parent_frame_id": parent_frame_id,
                "root_frame_id": root_frame_id,
                "new_messages": new_slice,
                "total_messages": len(messages),
            }

            # Create a virtual parent frame for bookmarker input
            virtual_parent = {
                "id": parent_frame_id,
                "root_frame_id": root_frame_id,
                "agent_name": "main",
            }

            # Spawn bookmarker as independent invocation (not a real child frame)
            child = create_child_frame(
                virtual_parent,  # type: ignore
                agent_name="bookmarker",
                input_data=input_data,
            )

            config = {"configurable": {"thread_id": child["id"]}}

            try:
                result = await bookmarker_graph.ainvoke(child, config=config)
            except Exception as e:
                _logger.debug(f"bookmarker invocation failed: {e}")
                continue

            # Extract bookmarks from result
            bookmarks = _extract_bookmarks(result)
            if bookmarks:
                try:
                    with open(output_file, "a") as f:
                        for bm in bookmarks:
                            f.write(
                                json.dumps({
                                    "timestamp": int(time.time()),
                                    "parent_frame_id": parent_frame_id,
                                    "at_message_index": len(messages),
                                    "bookmark": bm,
                                }) + "\n"
                            )
                    _logger.debug(
                        f"wrote {len(bookmarks)} bookmarks to {output_file}"
                    )
                except IOError as e:
                    _logger.warning(f"failed to write bookmarks: {e}")

            last_message_count = len(messages)

    except asyncio.CancelledError:
        _logger.info(f"bookmarker task cancelled for frame {parent_frame_id}")
        raise


async def spawn_bookmarker_background_task(
    parent_frame_id: str,
    *,
    root_frame_id: str,
    interval_seconds: int = 30,
    bookmarks_dir: Path | None = None,
    checkpointer: Any = None,
) -> asyncio.Task[None]:
    """Spawn a concurrent bookmarker task that periodically snapshots main frame.

    The task runs a background asyncio loop that:
    1. Reads the parent frame's latest checkpoint every `interval_seconds`
    2. Invokes the bookmarker agent on new messages since last check
    3. Writes extracted bookmarks to `<bookmarks_dir>/<root_frame_id>.jsonl`

    The task runs until explicitly cancelled (via task.cancel()).

    Args:
        parent_frame_id: the frame ID to monitor
        root_frame_id: session/root frame ID (for JSONL filename)
        interval_seconds: sleep interval between checkpoint polls (default 30)
        bookmarks_dir: output directory for bookmark JSONL files (default ~/.internagents/bookmarks)
        checkpointer: LangGraph checkpointer (if None, bookmarker is no-op)

    Returns:
        An asyncio.Task that runs the bookmarker loop. Caller should cancel() it when parent completes.

    Example:
        ```python
        task = await spawn_bookmarker_background_task(
            parent_frame_id=main_frame["id"],
            root_frame_id=main_frame["root_frame_id"],
            interval_seconds=30,
            checkpointer=my_checkpointer,
        )
        # ... main frame runs ...
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        ```
    """
    # If no checkpointer, create a no-op task
    if checkpointer is None:
        async def noop_task() -> None:
            try:
                while True:
                    await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                _logger.info(
                    f"bookmarker (no-op mode) cancelled for frame {parent_frame_id}"
                )
                raise

        return asyncio.create_task(noop_task())

    # Create the bookmarker loop task
    return asyncio.create_task(
        _bookmarker_loop(
            parent_frame_id=parent_frame_id,
            root_frame_id=root_frame_id,
            interval_seconds=interval_seconds,
            bookmarks_dir=bookmarks_dir or Path.home() / ".internagents" / "bookmarks",
            checkpointer=checkpointer,
        )
    )


async def create_and_run_root_frame_with_bookmarker(
    *,
    input_data: dict[str, Any],
    agent_name: AgentName = "main",
    system_prompt: str | None = None,
    checkpointer: Any = None,
    graph: Any | None = None,
    enable_bookmarker: bool = False,
    bookmarker_interval: int = 30,
) -> FrameState:
    """Convenience: run a root frame with an optional concurrent bookmarker.

    Spawns a bookmarker background task if enable_bookmarker=True,
    runs the main frame, and cancels the bookmarker when done.

    Args:
        input_data: user input that initiates the frame
        agent_name: which agent runs this frame (default "main")
        system_prompt: optional system prompt override
        checkpointer: LangGraph checkpointer (required for bookmarker)
        graph: optional LangGraph compiled agent graph
        enable_bookmarker: whether to run concurrent bookmarker (default False)
        bookmarker_interval: seconds between bookmarker checks (default 30)

    Returns:
        Terminal FrameState after frame completes and bookmarker is cancelled

    Example:
        ```python
        frame = await create_and_run_root_frame_with_bookmarker(
            input_data={"query": "..."},
            enable_bookmarker=True,
            checkpointer=my_checkpointer,
        )
        # Bookmarks written to ~/.internagents/bookmarks/<root_frame_id>.jsonl
        ```
    """
    frame = create_root_frame(
        agent_name=agent_name,
        input_data=input_data,
        system_prompt=system_prompt,
    )

    bookmarker_task = None
    if enable_bookmarker:
        bookmarker_task = await spawn_bookmarker_background_task(
            parent_frame_id=frame["id"],
            root_frame_id=frame["root_frame_id"],
            interval_seconds=bookmarker_interval,
            checkpointer=checkpointer,
        )

    try:
        # Run the main frame
        return await create_and_run_root_frame(
            input_data=input_data,
            agent_name=agent_name,
            system_prompt=system_prompt,
            checkpointer=checkpointer,
            graph=graph,
        )
    finally:
        # Cancel bookmarker task if it was created
        if bookmarker_task is not None:
            bookmarker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bookmarker_task


def _workspace_id_from_resource(resource_id: str = "local") -> str:
    """Compute stable workspace ID from resource path.

    Reads internagent.resources.json to find the resource's workspace path,
    then produces a stable 16-char ID using SHA256 (matching UI's workspaceIdForPath).

    Args:
        resource_id: Resource identifier (default "local")

    Returns:
        16-char hex string workspace ID, or "resource:{resource_id}" if no workspace found
    """
    try:
        resources_file = Path.cwd() / "internagent.resources.json"
        if not resources_file.exists():
            return f"resource:{resource_id}"

        with resources_file.open(encoding="utf-8") as f:
            resources_config = json.load(f)

        resources = resources_config.get("resources", [])
        for res in resources:
            if res.get("id") == resource_id:
                workspace_path = res.get("workspace")
                if workspace_path:
                    # Resolve to absolute path
                    workspace_abs = Path(workspace_path).expanduser().resolve()
                    workspace_str = str(workspace_abs)

                    # SHA256 hash, first 16 chars (matching UI behavior)
                    hash_hex = hashlib.sha256(workspace_str.encode()).hexdigest()
                    return hash_hex[:16]

        return f"resource:{resource_id}"

    except Exception as e:
        _logger.debug(f"workspace_id_from_resource failed: {e}")
        return f"resource:{resource_id}"


def _load_agent_config() -> dict[str, Any]:
    """Load deepagent.config.json.

    Returns empty dict if file doesn't exist or fails to parse.
    """
    try:
        from internagents.agent_graph import _agent_config_path

        config_file = _agent_config_path()
        if not config_file.exists():
            return {}
        with config_file.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        _logger.debug(f"_load_agent_config failed: {e}")
        return {}


def _save_agent_config(config: dict[str, Any]) -> None:
    """Save deepagent.config.json.

    Args:
        config: Full config dict to write back

    Returns: nothing on success; logs warning on failure
    """
    try:
        from internagents.agent_graph import _agent_config_path

        config_file = _agent_config_path()
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with config_file.open("w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        _logger.warning(f"_save_agent_config failed: {e}")


async def entry_root_frame(
    *,
    input_data: dict[str, Any],
    resource_id: str = "local",
    checkpointer: Any = None,
    graph: Any = None,
) -> FrameState:
    """Entry gate for root frame — routes to onboarding or main based on workspace status.

    If the resource's workspace has not completed onboarding, routes to "onboarding" agent.
    Once onboarding completes, marks the workspace in config and subsequent calls route to "main".

    Args:
        input_data: User input that initiates the frame
        resource_id: Resource identifier (default "local")
        checkpointer: LangGraph checkpointer (optional)
        graph: optional LangGraph compiled agent graph (optional)

    Returns:
        Terminal FrameState of the root frame (onboarding or main)

    Example:
        ```python
        # First call in a new workspace → routes to onboarding
        frame = await entry_root_frame(input_data={"query": "..."})

        # After onboarding completes, subsequent calls route to main
        frame = await entry_root_frame(input_data={"query": "..."})
        ```
    """
    # Compute workspace ID
    workspace_id = _workspace_id_from_resource(resource_id)

    # Load config
    config = _load_agent_config()

    # Check if this workspace has completed onboarding
    onboarding_completed = config.get("onboarding_completed_workspaces", {})
    if onboarding_completed.get(workspace_id, False):
        # Already onboarded → route to main
        return await create_and_run_root_frame(
            input_data=input_data,
            agent_name="main",
            checkpointer=checkpointer,
            graph=graph,
        )

    # Not yet onboarded → route to onboarding
    onboarding_frame = await create_and_run_root_frame(
        input_data=input_data,
        agent_name="onboarding",
        checkpointer=checkpointer,
        graph=graph,
    )

    # If onboarding completed, mark workspace and save config
    if onboarding_frame.get("status") == "completed":
        if "onboarding_completed_workspaces" not in config:
            config["onboarding_completed_workspaces"] = {}
        config["onboarding_completed_workspaces"][workspace_id] = True
        _save_agent_config(config)
        _logger.info(
            f"marked workspace {workspace_id[:8]} as onboarded"
        )

    return onboarding_frame

