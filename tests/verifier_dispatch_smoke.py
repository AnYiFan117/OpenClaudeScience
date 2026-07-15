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


def test_middleware_triggers_on_terminal_status_or_end_of_turn():
    """Checkpoint fires on: (a) LLM-set terminal status, OR (b) end-of-turn.

    End-of-turn = last message is AIMessage with no tool_calls (LangGraph
    routes to END there). Both are equivalent "the LLM produced its final
    answer this turn" signals.
    """
    config = {
        "verification": {
            "enabled": True,
            "checkpoint_message_threshold": 6,  # legacy, ignored now
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    # (a) frame_status=completed → should trigger
    state_done = {
        "messages": [HumanMessage(content=f"msg{i}") for i in range(2)],
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "completed",
    }
    assert middleware._should_checkpoint(state_done) is True

    # (b) frame_status=running but last message is AIMessage with no tool_calls
    state_end_of_turn = {
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(content="final answer", tool_calls=[]),
        ],
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "running",
    }
    assert middleware._should_checkpoint(state_end_of_turn) is True

    # NEITHER (mid-tool-call: last message is HumanMessage/ToolMessage,
    # frame_status still running) → should NOT trigger
    state_midturn = {
        "messages": [HumanMessage(content=f"msg{i}") for i in range(20)],
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "running",
    }
    assert middleware._should_checkpoint(state_midturn) is False

    # AIMessage WITH tool_calls (mid-turn, LLM asked for a tool) → NOT trigger
    state_pending_tool = {
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(
                content="",
                tool_calls=[{"id": "abc", "name": "some_tool", "args": {}}],
            ),
        ],
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "running",
    }
    assert middleware._should_checkpoint(state_pending_tool) is False

    print("✅ middleware_triggers_on_terminal_status_or_end_of_turn")


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


def test_middleware_does_not_skip_child_frame():
    """Work child frames should be reviewed at their own boundary.

    The old `parent_frame_id is not None → skip` guard was intentionally
    dropped: the middleware is wired only onto the `main` agent
    (agent_graph._filter_middlewares_for_agent), so it never runs on
    meta-children (reviewer/bookmarker/onboarding). When main is spawned
    as a work child via spawn_subframe, we WANT its end-of-turn to be
    reviewed.
    """
    config = {
        "verification": {
            "enabled": True,
            "checkpoint_message_threshold": 6,
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    state = {
        "messages": [HumanMessage(content=f"msg{i}") for i in range(7)],
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "parent_frame_id": "parent-frame-id",  # child frame
        "frame_status": "running",
    }

    should_suppress = middleware._should_suppress(state)
    assert should_suppress is False, "Child frames should NOT be suppressed anymore"
    print("✅ middleware_does_not_skip_child_frame")


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
        "frame_status": "completed",
    }

    # Mock the reviewer graph returned by get_agent_graph
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_reviewer_frame)
    with patch(
        "internagents.agent_graph.get_agent_graph", return_value=mock_graph
    ):
        result = await middleware.aafter_model(state, runtime=None)

    assert result is not None, "Should return state updates"
    assert "reviews" in result, "Result should have reviews (not messages — reviews are isolated from LLM input)"
    assert len(result["reviews"]) == 1, "Should have exactly one review entry"

    entry = result["reviews"][0]
    assert entry["status"] == "done"
    assert entry["verdict"] == "warn"
    assert entry["issues"] == ["hallucination detected"]
    assert entry["suggestions"] == ["verify sources"]
    assert entry["id"].startswith("review_")
    assert "at_message_index" in entry
    assert "bounce_count" in entry
    # Critical: NO messages field — review must not enter state.messages
    assert "messages" not in result, "reviews must NOT touch state.messages (CS isolation invariant)"

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


def test_extract_findings_code_fence_json():
    """Reviewer wrapped JSON in ```json ... ``` fence — must still parse verdict.

    This was the biggest source of misclassified 'warn' entries: the LLM said
    verdict=pass inside a markdown fence, direct json.loads() failed, and the
    old fallback slapped the whole fence text into `issues` with a fake warn.
    """
    fenced = (
        "Here are my findings:\n\n"
        "```json\n"
        '{"verdict": "pass", "issues": [], "suggestions": []}\n'
        "```"
    )
    frame = {"output_data": {}, "messages": [AIMessage(content=fenced)]}
    findings = _extract_findings_from_reviewer(frame)
    assert findings["verdict"] == "pass", f"Expected 'pass', got {findings['verdict']!r}"
    assert findings["issues"] == []
    assert "_parse_error" not in findings, "Fenced JSON must be recognized, not surfaced as parse_error"
    print("✅ extract_findings_code_fence_json")


def test_extract_findings_embedded_json_in_prose():
    """Reviewer wrote a prose preamble then bare JSON — regex fallback must find it."""
    prose_then_json = (
        "Looking at the transcript, everything traces to visible tool outputs.\n"
        "My verdict:\n"
        '{"verdict": "pass", "issues": [], "suggestions": ["consider adding a summary"]}'
    )
    frame = {"output_data": {}, "messages": [AIMessage(content=prose_then_json)]}
    findings = _extract_findings_from_reviewer(frame)
    assert findings["verdict"] == "pass"
    assert findings["suggestions"] == ["consider adding a summary"]
    assert "_parse_error" not in findings
    print("✅ extract_findings_embedded_json_in_prose")


def test_extract_findings_pure_prose_no_json():
    """Reviewer emitted only narrative — must return verdict=unknown, NOT force warn.

    The old fallback did `verdict='warn'; issues=[content[:200]]` which
    misclassified passing narratives as issues and truncated mid-sentence.
    """
    prose = (
        "Looking at this transcript carefully, the agent successfully "
        "tested the magnesium alloy calculation skills and used them "
        "for a design task. All reported values trace to visible tool "
        "outputs, and there are no fabrications or plan deviations "
        "worth flagging."
    )
    frame = {"output_data": {}, "messages": [AIMessage(content=prose)]}
    findings = _extract_findings_from_reviewer(frame)
    assert findings["verdict"] == "unknown", (
        f"Pure prose must NOT be classified as warn — got {findings['verdict']!r}"
    )
    assert findings["issues"] == [], "Prose must not be stuffed into issues"
    assert findings["_parse_error"] == prose, (
        "Full prose must be surfaced via _parse_error (no truncation)"
    )
    print("✅ extract_findings_pure_prose_no_json")


def test_extract_findings_structured_response_channel():
    """When response_format is wired in the future, extractor reads
    state['structured_response'] as the authoritative source."""
    frame = {
        "output_data": {},
        "structured_response": {
            "verdict": "warn",
            "issues": ["something odd"],
            "suggestions": [],
        },
    }
    findings = _extract_findings_from_reviewer(frame)
    assert findings["verdict"] == "warn"
    assert findings["issues"] == ["something odd"]
    print("✅ extract_findings_structured_response_channel")


async def test_middleware_parse_error_becomes_entry_error():
    """End-to-end: reviewer emits unparseable prose → entry.error carries full text,
    entry.verdict='unknown', entry.issues=[]."""
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
        "frame_id": "frame1",
        "root_frame_id": "root-parse-err",
        "frame_status": "completed",
    }

    long_prose = "A" * 500  # far more than the old 200-char slice
    mock_reviewer_frame = {
        "id": "reviewer1",
        "output_data": {},
        "messages": [AIMessage(content=long_prose)],
    }
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_reviewer_frame)

    with patch("internagents.agent_graph.get_agent_graph", return_value=mock_graph):
        result = await middleware.aafter_model(state, runtime=None)

    assert "reviews" in result and len(result["reviews"]) == 1
    entry = result["reviews"][0]
    assert entry["verdict"] == "unknown"
    assert entry["issues"] == []
    assert entry.get("error"), "entry.error must be set when reviewer output was unparseable"
    assert long_prose in entry["error"], "Full prose (not truncated) must appear in entry.error"
    _cleanup_root_frame("root-parse-err")
    print("✅ middleware_parse_error_becomes_entry_error")


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
        "frame_status": "completed",
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


async def test_middleware_no_cleanup_on_completed_status():
    """`completed` fires every turn under CS-aligned lifecycle — must NOT cleanup.

    If cleanup ran here, the bookmarker task would be cancelled + respawned
    on every turn boundary. Cleanup must fire only on permanent-terminal
    statuses (failed/cancelled/blocked).
    """
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
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "completed",
    }

    # Prime bounces
    _VERIFICATION_BOUNCES["root1"] = 1

    mock_reviewer_frame = {
        "id": "reviewer1",
        "output_data": {"verdict": "pass"},
    }

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_reviewer_frame)
    with patch("internagents.agent_graph.get_agent_graph", return_value=mock_graph):
        await middleware.aafter_model(state, runtime=None)

    # Bounces should persist across turns
    assert _VERIFICATION_BOUNCES.get("root1") == 1, "Should NOT clean up on 'completed'"

    # Cleanup manually
    _cleanup_root_frame("root1")
    print("✅ middleware_no_cleanup_on_completed_status")


async def test_middleware_cleanup_on_permanent_terminal_status():
    """Cleanup fires on permanent-terminal statuses (failed/cancelled/blocked)."""
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
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "failed",  # Permanent-terminal
    }

    _VERIFICATION_BOUNCES["root1"] = 1

    mock_reviewer_frame = {
        "id": "reviewer1",
        "output_data": {"verdict": "pass"},
    }

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_reviewer_frame)
    with patch("internagents.agent_graph.get_agent_graph", return_value=mock_graph):
        await middleware.aafter_model(state, runtime=None)

    assert "root1" not in _VERIFICATION_BOUNCES, "Should clean up on 'failed'"
    print("✅ middleware_cleanup_on_permanent_terminal_status")


async def test_reviews_never_touch_state_messages():
    """CRITICAL invariant: review findings go to state.reviews, NEVER state.messages.

    This is the CS-aligned isolation that prevents review content from
    re-entering the LLM's context — the root of the death-loop /
    context-pollution bugs we hit before switching channels.
    """
    config = {
        "verification": {
            "enabled": True,
            "bookmarks_enabled": False,
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    mock_reviewer_frame = {
        "id": "reviewer1",
        "output_data": {
            "verdict": "warn",
            "issues": ["Issue 1"],
            "suggestions": ["Fix this"],
        },
    }

    state = {
        "messages": [HumanMessage(content=f"msg{i}") for i in range(7)],
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "completed",
    }

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_reviewer_frame)
    with patch(
        "internagents.agent_graph.get_agent_graph", return_value=mock_graph
    ):
        result = await middleware.aafter_model(state, runtime=None)

    assert result is not None
    assert "reviews" in result and len(result["reviews"]) == 1
    entry = result["reviews"][0]
    assert entry["verdict"] == "warn"
    assert entry["issues"] == ["Issue 1"]
    assert entry["suggestions"] == ["Fix this"]

    # THE key invariant: nothing about the review has leaked into messages
    assert "messages" not in result

    _cleanup_root_frame("root1")
    print("✅ reviews_never_touch_state_messages")


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
            "verdict": "fail",  # only fail triggers veto (warn is informational)
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


async def test_end_of_turn_sets_frame_status_completed():
    """End-of-turn (running + AIMessage no tool_calls) → returned dict has frame_status='completed'.

    This is what canonicalizes the status so FrameRootMiddleware sees a
    terminal frame on the next turn and revives it (CS-aligned per-turn cycle).
    """
    config = {
        "verification": {
            "enabled": True,
            "bookmarks_enabled": False,
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    mock_reviewer_frame = {
        "id": "reviewer1",
        "output_data": {"verdict": "pass"},  # No veto
    }

    state = {
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(content="here is my final answer", tool_calls=[]),
        ],
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "running",  # will be canonicalized to completed
    }

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_reviewer_frame)
    with patch("internagents.agent_graph.get_agent_graph", return_value=mock_graph):
        result = await middleware.aafter_model(state, runtime=None)

    assert result is not None, "Should have run review + returned an update"
    assert result.get("frame_status") == "completed", (
        f"Expected frame_status='completed' (canonicalize on end-of-turn), got {result.get('frame_status')!r}"
    )

    _cleanup_root_frame("root1")
    print("✅ end_of_turn_sets_frame_status_completed")


async def test_end_of_turn_veto_reverts_to_running():
    """End-of-turn + verdict != pass + bounces < max → veto to 'running'.

    Even though the trigger was end-of-turn (frame_status still 'running'
    coming in), the veto path should activate and set frame_status='running'
    in the update (unchanged status, but incremented bounce). This forces
    another model turn.
    """
    config = {
        "verification": {
            "enabled": True,
            "bookmarks_enabled": False,
            "max_consecutive_bounces": 3,
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    _VERIFICATION_BOUNCES["root1"] = 0

    mock_reviewer_frame = {
        "id": "reviewer1",
        "output_data": {
            "verdict": "fail",  # only fail triggers veto (warn is informational)
            "issues": ["hallucination"],
        },
    }

    state = {
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(content="my answer", tool_calls=[]),
        ],
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "running",
    }

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_reviewer_frame)
    with patch("internagents.agent_graph.get_agent_graph", return_value=mock_graph):
        result = await middleware.aafter_model(state, runtime=None)

    assert result is not None
    assert result.get("frame_status") == "running", (
        f"Veto should keep status='running' to force another turn, got {result.get('frame_status')!r}"
    )
    assert _VERIFICATION_BOUNCES.get("root1") == 1, "Veto should increment bounces"

    _cleanup_root_frame("root1")
    print("✅ end_of_turn_veto_reverts_to_running")


async def test_warn_verdict_does_not_veto():
    """`warn` findings are shown but do NOT force main to re-loop.

    Regression guard: earlier bug had `verdict != "pass"` as veto criterion,
    which caused runaway loops when reviewer emitted warn on trivial chat
    turns. Only `fail` should veto.
    """
    config = {
        "verification": {
            "enabled": True,
            "bookmarks_enabled": False,
            "max_consecutive_bounces": 3,
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    _VERIFICATION_BOUNCES["root1"] = 0

    mock_reviewer_frame = {
        "id": "reviewer1",
        "output_data": {
            "verdict": "warn",  # warn should NOT veto
            "issues": ["minor presentation issue"],
        },
    }

    state = {
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(content="my answer", tool_calls=[]),
        ],
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "running",
    }

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=mock_reviewer_frame)
    with patch("internagents.agent_graph.get_agent_graph", return_value=mock_graph):
        result = await middleware.aafter_model(state, runtime=None)

    assert result is not None
    # warn + end-of-turn → canonicalize to completed, NOT veto to running
    assert result.get("frame_status") == "completed", (
        f"warn should canonicalize to 'completed' (not veto), got {result.get('frame_status')!r}"
    )
    # bounces must NOT increment on warn
    assert _VERIFICATION_BOUNCES.get("root1", 0) == 0, (
        f"warn should NOT increment bounces, got {_VERIFICATION_BOUNCES.get('root1')}"
    )

    _cleanup_root_frame("root1")
    print("✅ warn_verdict_does_not_veto")


async def test_reviewer_failure_writes_failed_entry():
    """Reviewer spawn error: a review entry with status='failed' + error must
    be written to state.reviews, and bounces bumped. No messages leak.
    """
    config = {
        "verification": {
            "enabled": True,
            "bookmarks_enabled": False,
        }
    }
    middleware = VerifierDispatchMiddleware(agent_config_dict=config)

    state = {
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(content="my answer", tool_calls=[]),
        ],
        "frame_id": "frame1",
        "root_frame_id": "root1",
        "frame_status": "running",
    }

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("internagents.agent_graph.get_agent_graph", return_value=mock_graph):
        result = await middleware.aafter_model(state, runtime=None)

    assert result is not None
    assert "reviews" in result and len(result["reviews"]) == 1
    entry = result["reviews"][0]
    assert entry["status"] == "failed"
    assert "reviewer spawn failed" in entry.get("error", "")

    assert "messages" not in result, "reviews channel must not touch state.messages"
    assert _VERIFICATION_BOUNCES.get("root1", 0) == 1

    _cleanup_root_frame("root1")
    print("✅ reviewer_failure_writes_failed_entry")


def run_all_tests():
    """Run all sync tests and return success status."""
    tests = [
        test_middleware_triggers_on_terminal_status_or_end_of_turn,
        test_middleware_skips_when_disabled,
        test_middleware_does_not_skip_child_frame,
        test_middleware_skips_after_max_bounces,
        test_extract_findings_from_reviewer_output_data,
        test_extract_findings_from_reviewer_fallback,
        test_extract_findings_code_fence_json,
        test_extract_findings_embedded_json_in_prose,
        test_extract_findings_pure_prose_no_json,
        test_extract_findings_structured_response_channel,
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
        test_middleware_parse_error_becomes_entry_error,
        test_middleware_bookmarker_only_fires_when_enabled,
        test_middleware_no_cleanup_on_completed_status,
        test_middleware_cleanup_on_permanent_terminal_status,
        test_reviews_never_touch_state_messages,
        test_veto_reverts_frame_status_to_running,
        test_end_of_turn_sets_frame_status_completed,
        test_end_of_turn_veto_reverts_to_running,
        test_warn_verdict_does_not_veto,
        test_reviewer_failure_writes_failed_entry,
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
