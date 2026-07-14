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
        "_last_review_msg_idx": 0,
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
        "_last_review_msg_idx": 0,
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
        "_last_review_msg_idx": 0,
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
        "_last_review_msg_idx": 0,
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
    """Middleware should inject findings as SystemMessage after reviewer."""
    config = {
        "verification": {
            "enabled": True,
            "checkpoint_message_threshold": 6,
            "bookmarks_enabled": False,
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    # Mock reviewer frame output
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
        "_last_review_msg_idx": 0,
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "running",
    }

    # Mock spawn_reviewer (import from frame_service where it's actually used)
    with patch("internagents.frame_service.spawn_reviewer", new_callable=AsyncMock) as mock_spawn:
        mock_spawn.return_value = mock_reviewer_frame

        # Run the middleware
        result = await middleware.aafter_model(state, runtime=None)

    assert result is not None, "Should return state updates"
    assert "messages" in result, "Result should have messages"
    assert len(result["messages"]) > 0, "Should inject SystemMessage"

    msg = result["messages"][0]
    assert isinstance(msg, SystemMessage), f"Should inject SystemMessage, got {type(msg)}"
    assert "Reviewer feedback" in msg.content or any(
        "Reviewer feedback" in str(b.get("text", "")) for b in msg.content_blocks if isinstance(b, dict)
    ), "Should have reviewer feedback in message"

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
        "_last_review_msg_idx": 0,
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "running",
    }

    # Mock reviewer frame output
    mock_reviewer_frame = {
        "id": "reviewer1",
        "output_data": {"verdict": "pass"},
    }

    with patch("internagents.frame_service.spawn_reviewer", new_callable=AsyncMock) as mock_spawn:
        mock_spawn.return_value = mock_reviewer_frame

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
        "_last_review_msg_idx": 0,
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

    with patch("internagents.frame_service.spawn_reviewer", new_callable=AsyncMock) as mock_spawn:
        mock_spawn.return_value = mock_reviewer_frame

        await middleware.aafter_model(state, runtime=None)

    # Verify cleanup happened
    assert "root1" not in _VERIFICATION_BOUNCES, "Should clean up bounces dict"
    print("✅ middleware_cleanup_on_terminal_status")


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
