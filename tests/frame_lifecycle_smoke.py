"""Smoke tests for Frame auto-lifecycle β semantics."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import HumanMessage, AIMessage
from internagents.frame_middleware import (
    _extract_objective_from_messages,
    FrameRootMiddleware,
    _frame_from_state,
)
from internagents.frame_state import TERMINAL_FRAME_STATUSES


def test_extract_objective_from_last_human_message():
    """Extract objective from the most recent HumanMessage."""
    messages = [
        HumanMessage(content="first task"),
        AIMessage(content="working on it"),
        HumanMessage(content="second task"),
    ]
    objective = _extract_objective_from_messages(messages)
    assert objective == "second task", f"Expected 'second task', got {objective!r}"
    print("✅ extract_objective_from_last_human_message finds most recent HumanMessage")


def test_extract_objective_raises_when_no_human_message():
    """Raise ValueError when messages have no HumanMessage."""
    messages = [AIMessage(content="response")]
    try:
        _extract_objective_from_messages(messages)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "no HumanMessage" in str(e)
    print("✅ extract_objective_raises_when_no_human_message")


def test_extract_objective_handles_multimodal_content():
    """Extract text from multimodal content blocks."""
    messages = [
        HumanMessage(
            content=[
                {"type": "text", "text": "task description"},
                {"type": "image_url", "image_url": {"url": "..."}},
            ]
        )
    ]
    objective = _extract_objective_from_messages(messages)
    assert objective == "task description", f"Expected 'task description', got {objective!r}"
    print("✅ extract_objective_handles_multimodal_content")


def test_root_middleware_creates_on_empty_state():
    """FrameRootMiddleware creates a new frame when state has no frame_id."""
    middleware = FrameRootMiddleware()
    state = {"messages": [HumanMessage(content="test objective")]}
    result = middleware.before_agent(state, runtime=None)
    assert result is not None, "Should create a frame"
    assert "frame_id" in result, f"Result missing frame_id: {result}"
    assert "input_data" in result, f"Result missing input_data: {result}"
    assert result["input_data"].get("objective") == "test objective"
    assert result["frame_status"] == "running"
    print("✅ root_middleware_creates_on_empty_state")


def test_root_middleware_skips_when_running_frame_exists():
    """FrameRootMiddleware skips creation when a running frame exists."""
    middleware = FrameRootMiddleware()
    state = {
        "messages": [HumanMessage(content="new objective")],
        "frame_id": "existing-frame",
        "frame_status": "running",
        "agent_name": "main",
        "root_frame_id": "root-id",
    }
    result = middleware.before_agent(state, runtime=None)
    assert result is None, "Should not create when running frame exists"
    print("✅ root_middleware_skips_when_running_frame_exists")


def test_root_middleware_revives_after_completed():
    """FrameRootMiddleware revives (keeps frame_id) after previous turn completed.

    CS-aligned semantics: 1 conversation = 1 persistent root frame. When a
    turn ends the status is `completed`; on the next user message, revive
    the SAME frame by flipping status back to `running` — do NOT create a
    fresh frame_id.
    """
    middleware = FrameRootMiddleware()
    state = {
        "messages": [
            HumanMessage(content="new task"),
        ],
        "frame_id": "completed-frame",
        "frame_status": "completed",
        "agent_name": "main",
        "root_frame_id": "root-id",
    }
    result = middleware.before_agent(state, runtime=None)
    assert result is not None, "Should return an update"
    assert "frame_id" not in result, "Must NOT create a new frame_id on revive"
    assert result.get("frame_status") == "running", "Should flip status to running"
    print("✅ root_middleware_revives_after_completed")


def test_root_middleware_revives_after_blocked():
    """FrameRootMiddleware revives after `blocked` status same as `completed`."""
    middleware = FrameRootMiddleware()
    state = {
        "messages": [HumanMessage(content="another task")],
        "frame_id": "blocked-frame",
        "frame_status": "blocked",
        "agent_name": "main",
        "root_frame_id": "root-id",
    }
    result = middleware.before_agent(state, runtime=None)
    assert result is not None
    assert "frame_id" not in result, "Must NOT create a new frame_id on revive"
    assert result.get("frame_status") == "running"
    print("✅ root_middleware_revives_after_blocked")


def test_context_middleware_always_injects_when_objective_present():
    """FrameContextMiddleware injects frame context whenever a frame is active (no objective guard)."""
    from internagents.frame_middleware import _active_frame, render_frame_context

    # Create a frame state with objective
    state = {
        "frame_id": "test-frame",
        "frame_status": "running",
        "agent_name": "main",
        "root_frame_id": "root-id",
        "input_data": {"objective": "test objective"},
        "tokens_used": 10,
        "time_used_seconds": 5,
    }

    frame = _active_frame(state)
    assert frame is not None, "Should get active frame"

    context = render_frame_context(frame)
    assert "test objective" in context, f"Context should include objective: {context}"
    assert "Task context:" in context, f"Context should use new wording: {context}"
    assert "Continue working within the active frame" not in context, "Old wording removed"
    print("✅ context_middleware_always_injects_when_objective_present")


if __name__ == "__main__":
    tests = [
        test_extract_objective_from_last_human_message,
        test_extract_objective_raises_when_no_human_message,
        test_extract_objective_handles_multimodal_content,
        test_root_middleware_creates_on_empty_state,
        test_root_middleware_skips_when_running_frame_exists,
        test_root_middleware_revives_after_completed,
        test_root_middleware_revives_after_blocked,
        test_context_middleware_always_injects_when_objective_present,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"❌ {t.__name__}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\n✅ Results: {passed}/{len(tests)} tests passed")
    if failed > 0:
        print(f"❌ {failed} tests failed")
        sys.exit(1)
