"""Smoke tests for VerifierDispatchMiddleware."""

import sys
import os
import json
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from internagents.verifier_dispatch_middleware import (
    VerifierDispatchMiddleware,
    _VERIFICATION_BOUNCES,
    _BACKGROUND_BOOKMARKER_TASKS,
    _cleanup_root_frame,
    _extract_findings_from_reviewer,
)


def test_middleware_triggers_at_threshold():
    """Middleware should trigger checkpoint when message delta >= threshold."""
    config = {
        "verification": {
            "enabled": True,
            "checkpoint_message_threshold": 6,
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    state = {
        "messages": [HumanMessage(content=f"msg{i}") for i in range(7)],
        "_last_review_msg_idx": 0,  # legacy state key, no longer read by middleware
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "running",
    }

    should_checkpoint = middleware._should_checkpoint(state)
    assert should_checkpoint is True, "Should checkpoint when delta >= 6"
    print("✅ middleware_triggers_at_threshold")


def test_middleware_skips_when_disabled():
    """Middleware should not checkpoint when verification.enabled=false."""
    config = {
        "verification": {
            "enabled": False,
            "checkpoint_message_threshold": 6,
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    state = {
        "messages": [HumanMessage(content=f"msg{i}") for i in range(7)],
        "_last_review_msg_idx": 0,  # legacy state key, no longer read by middleware
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "running",
    }

    should_checkpoint = middleware._should_checkpoint(state)
    assert should_checkpoint is False, "Should not checkpoint when disabled"
    print("✅ middleware_skips_when_disabled")


def test_middleware_skips_child_frame():
    """Middleware should not checkpoint for child frames (parent_frame_id is not None)."""
    config = {
        "verification": {
            "enabled": True,
            "checkpoint_message_threshold": 6,
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    state = {
        "messages": [HumanMessage(content=f"msg{i}") for i in range(7)],
        "_last_review_msg_idx": 0,  # legacy state key, no longer read by middleware
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "parent_frame_id": "parent-frame-id",  # Child frame!
        "frame_status": "running",
    }

    should_suppress = middleware._should_suppress(state)
    assert should_suppress is True, "Should suppress for child frames"
    print("✅ middleware_skips_child_frame")


def test_middleware_skips_after_max_bounces():
    """Middleware should suppress after 3 consecutive failures."""
    config = {
        "verification": {
            "enabled": True,
            "max_consecutive_bounces": 3,
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    # Set bounces to max
    _VERIFICATION_BOUNCES["root1"] = 3

    state = {
        "messages": [HumanMessage(content=f"msg{i}") for i in range(7)],
        "_last_review_msg_idx": 0,  # legacy state key, no longer read by middleware
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "running",
    }

    should_suppress = middleware._should_suppress(state)
    assert should_suppress is True, "Should suppress after max bounces"

    # Cleanup
    _cleanup_root_frame("root1")
    print("✅ middleware_skips_after_max_bounces")


async def test_async_middleware_injects_findings():
    """Middleware should inject findings as HumanMessage with [Auditor] prefix after reviewer."""
    config = {
        "verification": {
            "enabled": True,
            "checkpoint_message_threshold": 6,
            "bookmarks_enabled": False,
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    # Mock reviewer graph output (middleware calls get_agent_graph("local","reviewer").ainvoke)
    mock_reviewer_frame = {
        "id": "reviewer1",
        "output_data": {
            "verdict": "warn",
            "issues": ["hallucination detected"],
            "suggestions": ["verify sources"],
        },
    }

    state = {
        "messages": [HumanMessage(content=f"msg{i}") for i in range(7)],
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "running",
    }

    # Mock the reviewer graph returned by get_agent_graph
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_reviewer_frame)
    with patch(
        "internagents.agent_graph.get_agent_graph", return_value=mock_graph
    ):
        result = await middleware.aafter_model(state, runtime=None)

    assert result is not None, "Should return state updates"
    assert "messages" in result, "Result should have messages"
    assert len(result["messages"]) == 2, "Should inject AIMessage + ToolMessage pair"

    ai_msg, tool_msg = result["messages"]
    from langchain_core.messages import AIMessage as _AIM, ToolMessage as _TM
    assert isinstance(ai_msg, _AIM), f"First should be AIMessage, got {type(ai_msg)}"
    assert isinstance(tool_msg, _TM), f"Second should be ToolMessage, got {type(tool_msg)}"
    assert ai_msg.tool_calls and ai_msg.tool_calls[0]["name"] == "review", "AIMessage should have review tool_call"
    assert tool_msg.tool_call_id == ai_msg.tool_calls[0]["id"], "ToolMessage id must match tool_call id"
    assert '"verdict": "warn"' in tool_msg.content, "ToolMessage content should include verdict"
    assert ai_msg.additional_kwargs.get("_harness_notice") is True, "AIMessage should have _harness_notice"
    assert tool_msg.additional_kwargs.get("_harness_notice") is True, "ToolMessage should have _harness_notice"

    # Cleanup
    _cleanup_root_frame("root1")
    print("✅ async_middleware_injects_findings")


def test_extract_findings_from_reviewer_output_data():
    """Extract findings from output_data dict."""
    frame = {
        "output_data": {
            "verdict": "pass",
            "issues": ["none"],
            "suggestions": [],
        },
    }
    findings = _extract_findings_from_reviewer(frame)
    assert findings["verdict"] == "pass", f"Expected 'pass', got {findings['verdict']}"
    assert findings["issues"] == ["none"]
    print("✅ extract_findings_from_reviewer_output_data")


def test_extract_findings_from_reviewer_fallback():
    """Extract findings when output_data is empty (fallback)."""
    frame = {
        "output_data": {},
    }
    findings = _extract_findings_from_reviewer(frame)
    assert "verdict" in findings, "Should have verdict key"
    print("✅ extract_findings_from_reviewer_fallback")


async def test_middleware_bookmarker_only_fires_when_enabled():
    """Bookmarker should only spawn when bookmarks_enabled=true."""
    config = {
        "verification": {
            "enabled": True,
            "checkpoint_message_threshold": 6,
            "bookmarks_enabled": False,  # Disabled
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    state = {
        "messages": [HumanMessage(content=f"msg{i}") for i in range(7)],
        "_last_review_msg_idx": 0,  # legacy state key, no longer read by middleware
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "running",
    }

    # Mock reviewer frame output
    mock_reviewer_frame = {
        "id": "reviewer1",
        "output_data": {"verdict": "pass"},
    }

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_reviewer_frame)
    with patch("internagents.agent_graph.get_agent_graph", return_value=mock_graph):

        with patch("internagents.frame_service.spawn_bookmarker_background_task", new_callable=AsyncMock) as mock_bookmarker:
            await middleware.aafter_model(state, runtime=None)
            # Should NOT call bookmarker since bookmarks_enabled=False
            mock_bookmarker.assert_not_called()

    # Cleanup
    _cleanup_root_frame("root1")
    print("✅ middleware_bookmarker_only_fires_when_enabled")


async def test_middleware_cleanup_on_terminal_status():
    """Module-level state should be cleaned up when frame reaches terminal status."""
    config = {
        "verification": {
            "enabled": True,
            "checkpoint_message_threshold": 6,
            "bookmarks_enabled": False,
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    state = {
        "messages": [HumanMessage(content=f"msg{i}") for i in range(7)],
        "_last_review_msg_idx": 0,  # legacy state key, no longer read by middleware
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "completed",  # Terminal status
    }

    # Prime the state dict
    _VERIFICATION_BOUNCES["root1"] = 1

    mock_reviewer_frame = {
        "id": "reviewer1",
        "output_data": {"verdict": "pass"},
    }

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_reviewer_frame)
    with patch("internagents.agent_graph.get_agent_graph", return_value=mock_graph):

        await middleware.aafter_model(state, runtime=None)

    # Verify cleanup happened
    assert "root1" not in _VERIFICATION_BOUNCES, "Should clean up bounces dict"
    print("✅ middleware_cleanup_on_terminal_status")


async def test_findings_injected_as_human_message_with_auditor_prefix():
    """Findings should be injected as HumanMessage with [Auditor] prefix and _harness_notice metadata."""
    config = {
        "verification": {
            "enabled": True,
            "checkpoint_message_threshold": 6,
            "bookmarks_enabled": False,
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    mock_reviewer_frame = {
        "id": "reviewer1",
        "output_data": {
            "verdict": "warn",
            "issues": ["Issue 1", "Issue 2"],
            "suggestions": ["Fix this", "And that"],
        },
    }

    state = {
        "messages": [HumanMessage(content=f"msg{i}") for i in range(7)],
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "running",
    }

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_reviewer_frame)
    with patch(
        "internagents.agent_graph.get_agent_graph", return_value=mock_graph
    ):
        result = await middleware.aafter_model(state, runtime=None)

    assert result is not None, "Should return state updates"
    assert "messages" in result, "Result should have messages"
    assert len(result["messages"]) == 2, "Should inject AIMessage + ToolMessage pair"

    ai_msg, tool_msg = result["messages"]
    from langchain_core.messages import AIMessage as _AIM, ToolMessage as _TM

    # Verify tool-call shape (matches how tool activity is rendered in UI)
    assert isinstance(ai_msg, _AIM), f"First should be AIMessage, got {type(ai_msg)}"
    assert isinstance(tool_msg, _TM), f"Second should be ToolMessage, got {type(tool_msg)}"
    assert ai_msg.tool_calls, "AIMessage must carry tool_calls"
    assert ai_msg.tool_calls[0]["name"] == "review"
    assert tool_msg.tool_call_id == ai_msg.tool_calls[0]["id"]

    # Verify metadata flag on both messages
    assert ai_msg.additional_kwargs.get("_harness_notice") is True
    assert tool_msg.additional_kwargs.get("_harness_notice") is True

    # Verify findings payload is embedded in ToolMessage content
    assert '"verdict": "warn"' in tool_msg.content
    assert "Issue 1" in tool_msg.content
    assert "Fix this" in tool_msg.content

    # Cleanup
    _cleanup_root_frame("root1")
    print("✅ findings_injected_as_review_tool_call")


async def test_veto_reverts_frame_status_to_running():
    """Veto gate should revert frame_status to 'running' when findings have issues and bounces < max."""
    config = {
        "verification": {
            "enabled": True,
            "checkpoint_message_threshold": 6,
            "bookmarks_enabled": False,
            "max_consecutive_bounces": 3,
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    # Prime bounces to be just below max
    _VERIFICATION_BOUNCES["root1"] = 1

    mock_reviewer_frame = {
        "id": "reviewer1",
        "output_data": {
            "verdict": "warn",  # Not "pass", so veto should trigger
            "issues": ["Found an issue"],
            "suggestions": ["Fix it"],
        },
    }

    # Frame is in terminal status (completed)
    state = {
        "messages": [HumanMessage(content=f"msg{i}") for i in range(7)],
        "_last_review_msg_idx": 0,  # legacy state key, no longer read by middleware
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "completed",  # Terminal status — should be vetoed back to running
    }

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_reviewer_frame)
    with patch("internagents.agent_graph.get_agent_graph", return_value=mock_graph):

        result = await middleware.aafter_model(state, runtime=None)

    assert result is not None, "Should return state updates"
    assert "frame_status" in result, "Result should include frame_status"
    assert result["frame_status"] == "running", f"Frame status should be reverted to 'running', got {result['frame_status']}"

    # Verify bounce counter was incremented
    assert _VERIFICATION_BOUNCES.get("root1") == 2, f"Bounce count should be 2, got {_VERIFICATION_BOUNCES.get('root1')}"

    # Cleanup
    _cleanup_root_frame("root1")
    print("✅ veto_reverts_frame_status_to_running")


def run_all_tests():
    """Run all sync tests and return success status."""
    tests = [
        test_middleware_triggers_at_threshold,
        test_middleware_skips_when_disabled,
        test_middleware_skips_child_frame,
        test_middleware_skips_after_max_bounces,
        test_extract_findings_from_reviewer_output_data,
        test_extract_findings_from_reviewer_fallback,
    ]

    for test in tests:
        try:
            test()
        except Exception as e:
            print(f"❌ {test.__name__}: {e}")
            return False

    return True


async def run_all_async_tests():
    """Run all async tests and return success status."""
    tests = [
        test_async_middleware_injects_findings,
        test_middleware_bookmarker_only_fires_when_enabled,
        test_middleware_cleanup_on_terminal_status,
        test_findings_injected_as_human_message_with_auditor_prefix,
        test_veto_reverts_frame_status_to_running,
    ]

    for test in tests:
        try:
            await test()
        except Exception as e:
            print(f"❌ {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            return False

    return True


if __name__ == "__main__":
    # Run sync tests
    success = run_all_tests()

    # Run async tests
    if success:
        success = asyncio.run(run_all_async_tests())

    if success:
        print("\n✅ All verifier dispatch tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed")
        sys.exit(1)
