"""Frame service APIs for LangGraph orchestration.

This module provides the core frame lifecycle operations:
1. create_and_run_root_frame - start a new conversation
2. dispatch_child_frame_nowait / collect_child_frames / stop_child_frame - async child frame APIs
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


async def spawn_reviewer(
    parent: FrameState,
    *,
    review_target: dict[str, Any] | None = None,
    checkpointer: Any = None,
    graph: Any | None = None,
) -> FrameState:
    """Spawn a reviewer child frame targeting the parent's output.

    Uses the new async dispatch API (dispatch_child_frame_nowait + collect_child_frames)
    instead of direct graph.ainvoke. Waits for the reviewer to complete before returning.

    Args:
        parent: the parent FrameState (usually main agent)
        review_target: optional review target data (defaults to parent's output)
        checkpointer: LangGraph checkpointer (optional, unused but kept for compat)
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

    input_data = {
        "review_target": review_target or parent.get("output_data") or {},
    }

    desc = await dispatch_child_frame_nowait(
        parent,
        agent_name="reviewer",
        input_data=input_data,
    )

    # For sync-style callers, block on collect. Give reviewer generous timeout.
    results = await collect_child_frames([desc["frame_id"]], timeout=1800)
    result = results[0]

    # Return the terminal FrameState stored in _child_results.
    terminal = _child_results.pop(desc["frame_id"], None)
    if terminal is None:
        raise RuntimeError(f"reviewer frame {desc['frame_id']} produced no terminal state (status={result.get('status')})")
    return terminal


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


# Module-level in-memory registry for async delegate children.
#
# CS parity: children run as INDEPENDENT langgraph runs on the local-runtime,
# NOT as asyncio tasks in the parent's event loop (reentrant graph.ainvoke
# deadlocks in langgraph checkpointer / context-vars). `_child_metadata`
# stores {parent_frame_id, agent_name, started_at, name, run_id} per child
# so we can poll status via the langgraph_sdk client.
_child_metadata: dict[str, dict[str, Any]] = {}
_child_results: dict[str, dict[str, Any]] = {}  # terminal state cached after first successful collect
_frame_inboxes: dict[str, asyncio.Queue] = {}  # receiver_frame_id → inbox queue of messages
_parent_notifications: dict[str, asyncio.Queue] = {}  # frame_id → event bus (completion / message / child_landed)
_completion_pollers: dict[str, asyncio.Task[Any]] = {}  # child_frame_id → background poller task

# Kept for pytest compatibility (tests mocking asyncio.Task registry).
# Production dispatch does not populate this — HTTP-driven child runs are
# tracked via _child_metadata[run_id].
_running_children: dict[str, asyncio.Task[Any]] = {}

# Back-compat alias — some tests / older callers still expect this name.
_parent_inboxes = _frame_inboxes


def _push_notification(receiver_frame_id: str, notif: dict[str, Any]) -> None:
    """Push a notification into a frame's event bus (used by wait_for_notification)."""
    q = _parent_notifications.setdefault(receiver_frame_id, asyncio.Queue())
    try:
        q.put_nowait(notif)
    except asyncio.QueueFull:  # queues here are unbounded, but be defensive
        _logger.warning("notification queue full for %s; dropping", receiver_frame_id)


def drain_frame_inbox(frame_id: str) -> list[dict[str, Any]]:
    """Drain and return all pending inbox messages for a frame.

    Called by MessageInboxMiddleware.before_agent so the receiving agent's
    next model turn sees any messages that arrived while it was idle.
    """
    q = _frame_inboxes.get(frame_id)
    if q is None:
        return []
    msgs = []
    while not q.empty():
        try:
            msgs.append(q.get_nowait())
        except asyncio.QueueEmpty:
            break
    return msgs


def get_running_children_count(parent_frame_id: str) -> int:
    """Count children with metadata whose parent matches, and no result cached yet."""
    n = 0
    for fid, meta in _child_metadata.items():
        if meta.get("parent_frame_id") == parent_frame_id and fid not in _child_results:
            n += 1
    return n


def _resolve_child_assistant_id() -> str:
    """Return the assistant_id used for child runs.

    Port 2024 (coordinator) exposes multiple graphs (`agent_local`, `agent_remote1..8`);
    Port 22024 (runtime) exposes a single graph named `agent`. The right choice
    depends on which port we dispatch to.
    """
    import os
    override = os.environ.get("INTERNAGENTS_CHILD_ASSISTANT_ID")
    if override:
        return override
    role = (os.environ.get("INTERNAGENT_PROCESS_ROLE") or "").lower()
    return "agent" if role == "runtime" else "agent_local"


def _resolve_runtime_url() -> str:
    """Return the langgraph HTTP base URL where child runs are dispatched.

    Children MUST run in the same Python process as the parent so that
    in-memory registries (_parent_notifications, _frame_inboxes, etc.) are
    shared. That means: dispatch back to whichever port this process itself
    is serving. For a coordinator process this is INTERNAGENTS_BACKEND_PORT
    (default 2024); for a runtime process it's INTERNAGENTS_LOCAL_RUNTIME_PORT
    (default 22024). Env override: INTERNAGENTS_DISPATCH_URL wins over both.
    """
    import os
    override = os.environ.get("INTERNAGENTS_DISPATCH_URL")
    if override:
        return override
    role = (os.environ.get("INTERNAGENT_PROCESS_ROLE") or "").lower()
    if role == "runtime":
        port = os.environ.get("INTERNAGENTS_LOCAL_RUNTIME_PORT", "22024")
    else:
        port = os.environ.get("INTERNAGENTS_BACKEND_PORT", "2024")
    host = os.environ.get("INTERNAGENTS_LOCAL_RUNTIME_HOST", "127.0.0.1")
    return f"http://{host}:{port}"


_lg_client_cache: dict[str, Any] = {}


def _get_lg_client() -> Any:
    """Lazy singleton langgraph_sdk client aimed at the local-runtime.

    Cached per URL so tests can inject via env var reset.
    """
    from langgraph_sdk import get_client
    url = _resolve_runtime_url()
    client = _lg_client_cache.get(url)
    if client is None:
        client = get_client(url=url)
        _lg_client_cache[url] = client
    return client


async def _poll_child_completion(parent_frame_id: str, child_frame_id: str, run_id: str | None) -> None:
    """Poll a child's run until terminal, then push a completion notification.

    Runs as a background asyncio.Task in the same process. Uses the same
    langgraph_sdk client used for dispatch. Polling interval starts at 0.75s
    and backs off gently up to 3s. Exits when the child run reports one of:
    success / error / interrupted / timeout — pushing the terminal result
    into the parent's notification bus.
    """
    if not run_id:
        return
    client = _get_lg_client()
    interval = 0.75
    while True:
        try:
            run = await client.runs.get(thread_id=child_frame_id, run_id=run_id)
        except Exception as exc:
            _logger.warning(
                "completion poller: runs.get failed for child=%s: %s",
                child_frame_id[:8], exc,
            )
            await asyncio.sleep(2.0)
            interval = min(interval + 0.5, 3.0)
            continue
        status = run.get("status") if isinstance(run, dict) else getattr(run, "status", None)
        if status in ("success", "error", "interrupted", "timeout"):
            # Terminal — fetch final state and cache result + notify parent.
            try:
                state = await client.threads.get_state(thread_id=child_frame_id)
            except Exception as exc:
                state = {}
                _logger.warning("completion poller: get_state failed for %s: %s", child_frame_id[:8], exc)
            values = state.get("values", {}) if isinstance(state, dict) else {}
            output_data = values.get("output_data") or {}
            msgs = values.get("messages") or []
            if not output_data and msgs:
                last = msgs[-1]
                content = last.get("content") if isinstance(last, dict) else getattr(last, "content", "")
                if content:
                    text = content if isinstance(content, str) else \
                        "".join(b.get("text", "") for b in content if isinstance(b, dict))
                    if text:
                        output_data = {"response": text}
            terminal = {
                "frame_id": child_frame_id,
                "root_frame_id": values.get("root_frame_id") or child_frame_id,
                "parent_frame_id": values.get("parent_frame_id") or parent_frame_id,
                "agent_name": values.get("agent_name"),
                "status": "completed" if status == "success" else "failed",
                "output_data": output_data,
                "run_status": status,
            }
            _child_results[child_frame_id] = terminal
            _push_notification(parent_frame_id, {
                "notification_type": "completion",
                "sender_frame_id": child_frame_id,
                "payload": terminal,
                "created_at": time.time(),
            })
            _completion_pollers.pop(child_frame_id, None)
            return
        await asyncio.sleep(interval)
        interval = min(interval + 0.25, 3.0)


async def wait_for_frame_notifications(
    frame_id: str,
    *,
    timeout_seconds: float = 60.0,
    drain: bool = True,
) -> dict[str, Any]:
    """CS wait_for_notification analog.

    Return shape:
        {status: "received", notifications: [...]}    — got ≥1 notification
        {status: "timeout",  notifications: [], pending_work: {...}}
        {status: "error"}   — nothing to wait for (no running work + empty queue)
    """
    if timeout_seconds is None or timeout_seconds < 0:
        raise ValueError("wait_for_frame_notifications requires a non-negative timeout")
    if timeout_seconds > 1800:
        raise ValueError("wait_for_frame_notifications timeout exceeds 1800s cap")

    q = _parent_notifications.setdefault(frame_id, asyncio.Queue())
    collected: list[dict[str, Any]] = []

    # Drain anything already queued (non-blocking).
    while drain:
        try:
            collected.append(q.get_nowait())
        except asyncio.QueueEmpty:
            break

    if collected:
        return {"status": "received", "notifications": collected}

    running = get_running_children_count(frame_id)
    if running == 0:
        # No pending work AND empty queue → signal end of fan-out.
        return {"status": "error"}

    # Park until first notification or timeout.
    try:
        first = await asyncio.wait_for(q.get(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return {
            "status": "timeout",
            "notifications": [],
            "pending_work": {
                "children": running,
                "unread_notifications": 0,
            },
        }
    collected.append(first)
    # Drain anything that landed while we were waiting.
    while True:
        try:
            collected.append(q.get_nowait())
        except asyncio.QueueEmpty:
            break
    return {"status": "received", "notifications": collected}


async def dispatch_child_frame_nowait(
    parent: FrameState,
    *,
    agent_name: AgentName,
    input_data: dict[str, Any],
    system_prompt: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """CS host.delegate(wait=False) analog: fire-and-forget child frame dispatch.

    Creates a child frame and submits it as an INDEPENDENT langgraph run on
    the local-runtime via HTTP (langgraph_sdk client). Returns immediately
    with a running descriptor — the parent does not block. Results are
    collected later via `collect_child_frames` polling the runtime.

    Args:
        parent: the parent FrameState
        agent_name: which agent runs this child frame (stored in state; runtime
            assistant is fixed to "agent" — routing happens inside the graph)
        input_data: input specific to the child's task (must include "objective"
            or "task" — used as the seed HumanMessage content)
        system_prompt: optional system prompt override (persisted on the frame)
        name: optional human label (returned in descriptor for the caller)

    Returns:
        {frame_id, root_frame_id, parent_frame_id, agent_name, name, status:"running", dispatched:True, run_id}
    """
    child = create_child_frame(
        parent,
        agent_name,
        input_data=input_data,
        system_prompt=system_prompt,
    )
    child = update_frame_status(child, "running")

    # Seed the initial HumanMessage from input_data. FrameRootMiddleware
    # requires a non-empty HumanMessage before it will accept the state.
    if input_data.get("objective"):
        seed_content = str(input_data["objective"])
    elif input_data.get("task"):
        seed_content = str(input_data["task"])
    elif input_data:
        seed_content = json.dumps(input_data, ensure_ascii=False)
    else:
        seed_content = f"Sub-task delegated to agent={agent_name}"

    # Build the child's initial STATE using the top-level field names that
    # frame_middleware._frame_from_state expects (frame_id / frame_status /
    # root_frame_id / parent_frame_id / agent_name). Otherwise the child's
    # FrameRootMiddleware treats the state as "no frame" and creates a new
    # ROOT frame — clobbering parent_frame_id and breaking send_frame_message.
    initial_state: dict[str, Any] = {
        "messages": [{"type": "human", "content": seed_content}],
        "frame_id": child["id"],
        "root_frame_id": child["root_frame_id"],
        "parent_frame_id": child["parent_frame_id"],
        "agent_name": agent_name,
        "frame_status": child.get("status", "running"),
        "tokens_used": 0,
        "time_used_seconds": 0,
        "input_data": child.get("input_data") or {},
    }

    client = _get_lg_client()

    # Ensure a thread exists for this child (idempotent — reuse if already there).
    try:
        await client.threads.create(thread_id=child["id"])
    except Exception:
        # Thread may already exist from a prior dispatch attempt; ignore.
        pass

    # Fire the run WITHOUT awaiting its completion. `runs.create` returns the
    # run descriptor once the run is queued.
    run = await client.runs.create(
        thread_id=child["id"],
        assistant_id=_resolve_child_assistant_id(),
        input=initial_state,
        multitask_strategy="reject",
    )
    run_id = run.get("run_id") if isinstance(run, dict) else getattr(run, "run_id", None)

    _child_metadata[child["id"]] = {
        "parent_frame_id": parent["id"],
        "agent_name": agent_name,
        "started_at": time.time(),
        "name": name,
        "run_id": run_id,
    }
    _logger.info(
        "dispatch_child_frame_nowait: parent=%s child=%s agent=%s run_id=%s",
        parent["id"][:8], child["id"][:8], agent_name, str(run_id)[:12],
    )

    # Kick off a background poller that pushes a completion notification to the
    # parent's event bus when this child's run reaches terminal state. This is
    # what makes `wait_for_notification` truly event-driven — the parent parks
    # once, and gets woken by whichever child lands first.
    _completion_pollers[child["id"]] = asyncio.create_task(
        _poll_child_completion(parent["id"], child["id"], run_id),
        name=f"completion-poller-{child['id'][:8]}",
    )

    # CS "child_landed" notice — sent as soon as the child is spawned so the
    # parent's wait_for_notification sees dispatched work exists.
    _push_notification(parent["id"], {
        "notification_type": "child_landed",
        "sender_frame_id": child["id"],
        "payload": {"frame_id": child["id"], "agent_name": agent_name, "name": name},
        "created_at": time.time(),
    })

    return {
        "frame_id": child["id"],
        "root_frame_id": child["root_frame_id"],
        "parent_frame_id": child.get("parent_frame_id"),
        "agent_name": agent_name,
        "name": name,
        "status": "running",
        "dispatched": True,
        "run_id": run_id,
    }



async def collect_child_frames(
    frame_ids: list[str],
    *,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """CS host.collect() analog: collect results for wait=False children.

    Blocks up to `timeout` seconds waiting for children to reach terminal state.
    Each result dict includes frame_id, status, output_data (if terminal).

    Args:
        frame_ids: list of frame IDs to collect results for
        timeout: maximum seconds to wait (default 30, max 1800); required (None rejected)

    Returns:
        List of result dicts, one per frame_id in order
        - terminal: {frame_id, status, output_data, ...}
        - still running: {frame_id, status: "running"}
    """
    if timeout is None:
        raise ValueError("collect_child_frames requires a bounded timeout (int/float, not None)")
    if timeout > 1800:
        raise ValueError("collect_child_frames timeout exceeds 1800s cap")

    deadline = asyncio.get_running_loop().time() + timeout
    results: dict[str, dict[str, Any]] = {}
    pending = set(frame_ids)

    client = _get_lg_client()

    while pending:
        for fid in list(pending):
            # Cached terminal → return immediately
            if fid in _child_results:
                results[fid] = _terminal_dict(_child_results[fid])
                pending.discard(fid)
                continue
            meta = _child_metadata.get(fid)
            if meta is None:
                # Fallback for test-mode asyncio.Task registry
                task = _running_children.get(fid)
                if task is None:
                    results[fid] = {"frame_id": fid, "status": "unknown", "error": "not tracked"}
                    pending.discard(fid)
                continue
            run_id = meta.get("run_id")
            if not run_id:
                results[fid] = {"frame_id": fid, "status": "unknown", "error": "no run_id"}
                pending.discard(fid)
                continue
            # Poll the child run's status via langgraph_sdk
            try:
                run = await client.runs.get(thread_id=fid, run_id=run_id)
            except Exception as exc:
                results[fid] = {"frame_id": fid, "status": "unknown", "error": f"runs.get failed: {exc}"}
                pending.discard(fid)
                continue
            run_status = run.get("status") if isinstance(run, dict) else getattr(run, "status", None)
            if run_status in ("success", "error", "interrupted", "timeout"):
                # Terminal — fetch final state
                try:
                    state = await client.threads.get_state(thread_id=fid)
                except Exception as exc:
                    results[fid] = {"frame_id": fid, "status": "failed", "error": f"threads.get_state failed: {exc}"}
                    pending.discard(fid)
                    continue
                values = state.get("values", {}) if isinstance(state, dict) else {}
                output_data = values.get("output_data") or {}
                msgs = values.get("messages") or []
                # Include last AI-message content as convenience if output_data is empty
                if not output_data and msgs:
                    last = msgs[-1]
                    content = last.get("content") if isinstance(last, dict) else getattr(last, "content", "")
                    if content:
                        text = content if isinstance(content, str) else \
                            "".join(b.get("text", "") for b in content if isinstance(b, dict))
                        if text:
                            output_data = {"response": text}
                terminal = {
                    "frame_id": fid,
                    "root_frame_id": values.get("root_frame_id") or fid,
                    "parent_frame_id": values.get("parent_frame_id"),
                    "agent_name": values.get("agent_name") or (meta.get("agent_name") if meta else None),
                    "status": "completed" if run_status == "success" else "failed",
                    "output_data": output_data,
                    "run_status": run_status,
                }
                _child_results[fid] = terminal
                results[fid] = terminal
                pending.discard(fid)

        if not pending:
            break

        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break

        await asyncio.sleep(min(1.0, remaining))

    # Remaining pending → running descriptors
    for fid in pending:
        results[fid] = {"frame_id": fid, "status": "running"}

    return [results[fid] for fid in frame_ids]


async def stop_child_frame(frame_id: str, *, reason: str | None = None) -> dict[str, Any]:
    """CS host.stop_child analog: cancel a running child frame's run via the runtime API.

    Args:
        frame_id: the child frame ID to stop
        reason: optional cancellation reason (echoed in the return dict)

    Returns:
        Terminal state dict for the frame (status typically "cancelled" or "failed").
    """
    meta = _child_metadata.get(frame_id)
    # Test-mode fallback: cancel local asyncio task
    if meta is None:
        task = _running_children.get(frame_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        result = _child_results.get(frame_id, {"id": frame_id, "status": "unknown"})
        terminal = _terminal_dict(result)
        if reason:
            terminal["cancel_reason"] = reason
        return terminal

    run_id = meta.get("run_id")
    client = _get_lg_client()
    # Cancel the completion poller BEFORE issuing the cancel HTTP so it doesn't
    # race and try to notify the parent as a success.
    poller = _completion_pollers.pop(frame_id, None)
    if poller is not None and not poller.done():
        poller.cancel()
        try:
            await poller
        except (asyncio.CancelledError, Exception):
            pass
    if run_id:
        try:
            await client.runs.cancel(thread_id=frame_id, run_id=run_id, action="interrupt")
        except Exception as exc:
            _logger.warning("stop_child_frame: cancel failed for %s: %s", frame_id, exc)

    # Read whatever state the child persisted before cancel
    try:
        state = await client.threads.get_state(thread_id=frame_id)
        values = state.get("values", {}) if isinstance(state, dict) else {}
        terminal = {
            "frame_id": frame_id,
            "root_frame_id": values.get("root_frame_id") or frame_id,
            "parent_frame_id": values.get("parent_frame_id"),
            "agent_name": values.get("agent_name") or meta.get("agent_name"),
            "status": "cancelled",
            "output_data": values.get("output_data") or {},
        }
    except Exception:
        terminal = {"frame_id": frame_id, "status": "cancelled", "output_data": {}}
    if reason:
        terminal["cancel_reason"] = reason
    _child_results[frame_id] = terminal
    return terminal


def _terminal_dict(state: FrameState | dict[str, Any]) -> dict[str, Any]:
    """Convert a FrameState to a terminal descriptor dict for tool returns."""
    frame_id = state.get("id") or state.get("frame_id", "unknown")
    return {
        "frame_id": frame_id,
        "root_frame_id": state.get("root_frame_id", "unknown"),
        "parent_frame_id": state.get("parent_frame_id"),
        "agent_name": state.get("agent_name"),
        "status": state.get("status", "unknown"),
        "output_data": state.get("output_data") or {},
    }


def list_child_frames_by_parent(parent_frame_id: str, *, only_running: bool = True) -> list[dict[str, Any]]:
    """List child frames for a given parent.

    Args:
        parent_frame_id: the parent's frame ID
        only_running: if True, only return running children; if False, include terminal children

    Returns:
        List of dicts with frame_id, agent_name, status, etc.
    """
    children = []

    # Scan _child_metadata for children with this parent
    for frame_id, metadata in _child_metadata.items():
        if metadata.get("parent_frame_id") == parent_frame_id:
            status = "running"
            if frame_id in _child_results:
                status = _child_results[frame_id].get("status", "unknown")
            elif frame_id not in _running_children:
                status = "unknown"

            if only_running and status != "running":
                continue

            children.append({
                "frame_id": frame_id,
                "agent_name": metadata.get("agent_name"),
                "status": status,
                "started_at": metadata.get("started_at"),
                "name": metadata.get("name"),
            })

    return children


async def send_message_to_frame(
    sender: FrameState,
    target: str,
    message: str,
    kind: str,
) -> dict[str, Any]:
    """Send a message to a DIRECT parent or DIRECT child frame.

    CS topology rules:
    - `target="parent"` OR target == sender.parent_frame_id → parent path
    - `target` is a frame_id whose parent_frame_id == sender["id"] → child path
    - Anything else refused (siblings, grandparents, arbitrary ids)

    Delivery:
    - Parent target: message queued into `_frame_inboxes[parent_id]` and a
      `message` notification pushed into `_parent_notifications[parent_id]`.
      Parent's MessageInboxMiddleware drains the inbox on its next turn.
    - Direct child target: same in-memory queue for a running child; if the
      child is idle/completed we also try to append via langgraph_sdk
      `threads.update_state` so the message becomes durable and a subsequent
      resume-run sees it. When the child is COMPLETED we additionally spawn a
      new run to "resume" the child with the message as fresh input.
    """
    sender_id = sender["id"]
    sender_parent_id = sender.get("parent_frame_id")

    # ------ Parent path ------
    if target == "parent" or (sender_parent_id and target == sender_parent_id):
        parent_id = sender_parent_id
        if not parent_id:
            return {"status": "refused", "reason": "no parent frame"}
        _frame_inboxes.setdefault(parent_id, asyncio.Queue()).put_nowait({
            "from_frame_id": sender_id,
            "message": message,
            "kind": kind,
            "created_at": time.time(),
        })
        _push_notification(parent_id, {
            "notification_type": "message",
            "sender_frame_id": sender_id,
            "payload": {"message": message, "kind": kind},
            "created_at": time.time(),
        })
        return {"status": "sent", "detail": {"target_parent": parent_id}}

    # ------ Direct child path ------
    meta = _child_metadata.get(target)
    if meta is None or meta.get("parent_frame_id") != sender_id:
        return {"status": "refused", "reason": f"target {target} is not a direct parent or child"}

    # In-process inbox (drained by receiver's middleware next turn if running).
    _frame_inboxes.setdefault(target, asyncio.Queue()).put_nowait({
        "from_frame_id": sender_id,
        "message": message,
        "kind": kind,
        "created_at": time.time(),
    })

    # If the child has a terminal result cached, treat this as a "resume" —
    # start a fresh langgraph run on the same thread with the new human input.
    if target in _child_results:
        client = _get_lg_client()
        seed = f"[From {sender_id[:8]}] {message}"
        try:
            run = await client.runs.create(
                thread_id=target,
                assistant_id=_resolve_child_assistant_id(),
                input={"messages": [{"type": "human", "content": seed}]},
                multitask_strategy="reject",
            )
            new_run_id = run.get("run_id") if isinstance(run, dict) else getattr(run, "run_id", None)
            # Refresh metadata so subsequent collect/stop track the new run.
            _child_metadata[target]["run_id"] = new_run_id
            _child_results.pop(target, None)  # cached terminal no longer valid
            # Reinstall completion poller on the new run.
            _completion_pollers[target] = asyncio.create_task(
                _poll_child_completion(sender_id, target, new_run_id),
                name=f"completion-poller-{target[:8]}-resume",
            )
            return {"status": "resumed", "detail": {"target_child": target, "run_id": new_run_id}}
        except Exception as exc:
            _logger.warning("send_message_to_frame: resume run failed for child=%s: %s", target, exc)
            return {"status": "refused", "reason": f"resume run failed: {exc}"}

    # Child still running — the in-process inbox is enough; child's
    # MessageInboxMiddleware picks it up on next model turn. Also push into
    # the child's own notification queue so a listening wait_for_notification
    # inside the child (rare, but supported) can react.
    _push_notification(target, {
        "notification_type": "message",
        "sender_frame_id": sender_id,
        "payload": {"message": message, "kind": kind},
        "created_at": time.time(),
    })
    return {"status": "injected", "detail": {"target_child": target}}

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

