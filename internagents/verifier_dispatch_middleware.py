"""Verification gate middleware for InternAgentS.

VerifierDispatchMiddleware implements synchronous review gating: after every
N messages (checkpoint threshold) or on frame status transition, it blocks
execution, spawns a reviewer child frame, extracts findings, and injects them
back into main's message stream as a SystemMessage. This allows the main LLM
to react to reviewer feedback in the same conversation.

Key design:
- Synchronous (blocking) gate: main waits for reviewer to complete
- Findings injection: reviewer output becomes a SystemMessage in main's messages
- Bounce limit: consecutive reviewer failures skip gating after 3 failures
- Bookmarker fire-and-forget: optional background task for keyword extraction
- JSONL logging: verdict summary written to ~/.internagents/verdicts/<root_frame_id>.jsonl
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from internagents.frame_state import (
    TERMINAL_FRAME_STATUSES,
)

_logger = logging.getLogger(__name__)

_FRAME_DEBUG = os.getenv("INTERNAGENT_FRAME_DEBUG", "").lower() in {"1", "true", "yes", "on"}


def _dbg(msg: str) -> None:
    """Print a Verifier debug line when INTERNAGENT_FRAME_DEBUG=1."""
    if _FRAME_DEBUG:
        print(f"🔍 [Verifier] {msg}", flush=True)


def _frame_from_state(state: dict[str, Any]) -> dict[str, Any] | None:
    """Reconstruct a FrameState from top-level state fields."""
    if not isinstance(state, dict):
        return None
    frame_id = state.get("frame_id")
    if not isinstance(frame_id, str) or not frame_id:
        return None
    return {
        "id": frame_id,
        "root_frame_id": state.get("root_frame_id") or frame_id,
        "parent_frame_id": state.get("parent_frame_id"),
        "agent_name": state.get("agent_name", "main"),
        "status": state.get("frame_status", "running"),
    }


# Module-level state keyed by root_frame_id
_VERIFICATION_BOUNCES: dict[str, int] = {}
_BACKGROUND_BOOKMARKER_TASKS: dict[str, asyncio.Task] = {}


def _cleanup_root_frame(root_frame_id: str) -> None:
    """Clean up module-level state for a root frame."""
    _VERIFICATION_BOUNCES.pop(root_frame_id, None)
    task = _BACKGROUND_BOOKMARKER_TASKS.pop(root_frame_id, None)
    if task is not None:
        task.cancel()


def _append_to_system_message(
    system_message: SystemMessage | None,
    text: str,
) -> SystemMessage:
    """Append text to existing SystemMessage or create new one."""
    new_content: list[dict[str, Any]] = (
        list(system_message.content_blocks) if system_message else []
    )
    if new_content:
        text = f"\n\n{text}"
    new_content.append({"type": "text", "text": text})
    return SystemMessage(content_blocks=new_content)


def _extract_findings_from_reviewer(reviewer_frame: dict[str, Any]) -> dict[str, Any]:
    """Extract structured findings from reviewer frame output.

    Tries to parse output_data as JSON matching the reviewer output_schema,
    falls back to raw text from last AIMessage.

    Returns dict with keys: verdict, issues, suggestions (best-effort)
    """
    findings: dict[str, Any] = {
        "verdict": "unknown",
        "issues": [],
        "suggestions": [],
    }

    # Try output_data first
    output_data = reviewer_frame.get("output_data", {})
    if isinstance(output_data, dict):
        if "verdict" in output_data:
            findings["verdict"] = output_data["verdict"]
        if "issues" in output_data:
            findings["issues"] = output_data["issues"]
        if "suggestions" in output_data:
            findings["suggestions"] = output_data["suggestions"]
        if findings["verdict"] != "unknown":
            return findings

    # Try last AIMessage content as JSON
    messages = reviewer_frame.get("messages", [])
    if messages:
        last_msg = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if last_msg is not None:
            content = last_msg.content
            if isinstance(content, str):
                try:
                    data = json.loads(content)
                    if isinstance(data, dict):
                        if "verdict" in data:
                            findings["verdict"] = data.get("verdict", "unknown")
                        if "issues" in data:
                            findings["issues"] = data.get("issues", [])
                        if "suggestions" in data:
                            findings["suggestions"] = data.get("suggestions", [])
                        return findings
                except (json.JSONDecodeError, ValueError):
                    pass
            # Fallback: treat content as raw text verdict
            if isinstance(content, str) and content.strip():
                findings["verdict"] = "warn"
                findings["issues"] = [content[:200]]  # First 200 chars

    return findings


@dataclass
class VerifierDispatchMiddleware(AgentMiddleware):
    """Synchronous review gate middleware for main agent.

    Checkpoints after N messages or on frame status transitions,
    spawns a reviewer child frame, and injects findings back into main.
    """

    agent_config_dict: dict[str, Any]

    def __post_init__(self) -> None:
        """Validate config and check for multi-worker warnings."""
        web_concurrency = int(os.getenv("WEB_CONCURRENCY", "1"))
        if web_concurrency > 1:
            _logger.warning(
                f"VerifierDispatchMiddleware running with WEB_CONCURRENCY={web_concurrency}. "
                "Module-level state dict will not be synchronized across workers. "
                "Consider switching to single-worker deployment."
            )

    @property
    def name(self) -> str:
        return "VerifierDispatchMiddleware"

    def _get_verification_config(self) -> dict[str, Any]:
        """Read verification config from agent_config_dict."""
        return self.agent_config_dict.get("verification", {})

    def _should_checkpoint(self, state: dict[str, Any]) -> bool:
        """Check if checkpoint should trigger based on message count or frame status."""
        config = self._get_verification_config()

        if not config.get("enabled", True):
            return False

        messages = state.get("messages", [])
        last_review_idx = state.get("_last_review_msg_idx", 0)
        threshold = config.get("checkpoint_message_threshold", 6)

        # Condition 1: message delta exceeded
        if len(messages) - last_review_idx >= threshold:
            return True

        # Condition 2: frame status transition to completed/pending_user_input
        frame_status = state.get("frame_status", "running")
        if frame_status in {"completed", "pending_user_input"}:
            return True

        return False

    def _should_suppress(self, state: dict[str, Any]) -> bool:
        """Check if verification should be suppressed by guards."""
        # Guard 1: skip if this is a child frame
        if state.get("parent_frame_id") is not None:
            return True

        # Guard 2: config disabled
        config = self._get_verification_config()
        if not config.get("enabled", True):
            return True

        # Guard 3: max bounces exceeded
        root_frame_id = state.get("root_frame_id")
        if root_frame_id:
            max_bounces = config.get("max_consecutive_bounces", 3)
            bounces = _VERIFICATION_BOUNCES.get(root_frame_id, 0)
            if bounces >= max_bounces:
                return True

        # Guard 4: no active frame
        frame = _frame_from_state(state)
        if frame is None:
            return True

        return False

    async def _run_reviewer(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Spawn reviewer and extract findings."""
        from internagents.frame_service import spawn_reviewer

        root_frame_id = state.get("root_frame_id")
        frame = _frame_from_state(state)

        if frame is None:
            return None

        try:
            # Reconstruct parent frame for reviewer input
            parent_frame = {
                "id": frame["id"],
                "root_frame_id": frame["root_frame_id"],
                "agent_name": "main",
                "status": "running",
            }

            # Build review target: current messages + frame context
            review_target = {
                "messages": state.get("messages", []),
                "frame_id": frame["id"],
            }

            _dbg(f"CHECKPOINT delta={len(state.get('messages', [])) - state.get('_last_review_msg_idx', 0)} → await reviewer")

            # Synchronous wait for reviewer
            reviewer_frame = await spawn_reviewer(
                parent=parent_frame,
                review_target=review_target,
            )

            _dbg(f"REVIEWER done status={reviewer_frame.get('status')}")

            return reviewer_frame

        except Exception as e:
            _logger.exception(f"reviewer spawn failed: {e}")
            # Increment bounce count on any exception
            if root_frame_id:
                _VERIFICATION_BOUNCES[root_frame_id] = _VERIFICATION_BOUNCES.get(root_frame_id, 0) + 1
            return None

    def _inject_findings(
        self,
        state: dict[str, Any],
        findings: dict[str, Any],
    ) -> dict[str, Any]:
        """Inject findings as HumanMessage with [Auditor] prefix and return state updates.

        The findings are formatted as a user-role message so the main LLM treats it
        as a real interlocutor input (matching Claude Science behavior). The message
        includes metadata flag _harness_notice=true for special UI rendering.
        """
        verdict = findings.get("verdict", "unknown")
        issues = findings.get("issues", [])
        suggestions = findings.get("suggestions", [])
        bounce_count = _VERIFICATION_BOUNCES.get(state.get("root_frame_id", ""), 0)

        issues_str = "\n".join(f"- {issue}" for issue in (issues or []))
        suggestions_str = "\n".join(f"- {suggestion}" for suggestion in (suggestions or []))

        # Format: [Auditor] verdict=... bounce_count=... on first line, then structured output
        auditor_text = f"[Auditor] verdict={verdict} bounce_count={bounce_count}"
        if issues_str:
            auditor_text += f"\n\nIssues:\n{issues_str}"
        if suggestions_str:
            auditor_text += f"\n\nSuggestions:\n{suggestions_str}"

        # Inject as HumanMessage with harness metadata
        auditor_msg = HumanMessage(
            content=auditor_text,
            additional_kwargs={"_harness_notice": True},
        )

        _dbg(f"INJECT HumanMessage verdict={verdict} issues_count={len(issues or [])} bounce_count={bounce_count}")

        return {
            "messages": [auditor_msg],
            "_last_review_msg_idx": len(state.get("messages", [])),
            "_last_frame_status": state.get("frame_status", "running"),
        }

    def _write_verdict_jsonl(
        self,
        root_frame_id: str,
        findings: dict[str, Any],
        at_message_index: int,
    ) -> None:
        """Write verdict to JSONL file for debugging."""
        try:
            verdicts_dir = Path.home() / ".internagents" / "verdicts"
            verdicts_dir.mkdir(parents=True, exist_ok=True)

            output_file = verdicts_dir / f"{root_frame_id}.jsonl"

            verdict_record = {
                "timestamp": int(time.time()),
                "root_frame_id": root_frame_id,
                "at_message_index": at_message_index,
                "verdict": findings.get("verdict"),
                "issues": findings.get("issues", []),
                "suggestions": findings.get("suggestions", []),
            }

            with open(output_file, "a") as f:
                f.write(json.dumps(verdict_record) + "\n")

        except Exception as e:
            _logger.warning(f"failed to write verdict JSONL: {e}")

    async def _maybe_spawn_bookmarker(self, state: dict[str, Any]) -> None:
        """Spawn bookmarker background task if enabled."""
        config = self._get_verification_config()
        if not config.get("bookmarks_enabled", False):
            return

        root_frame_id = state.get("root_frame_id")
        if not root_frame_id:
            return

        # Avoid spawning duplicate bookmarker
        if root_frame_id in _BACKGROUND_BOOKMARKER_TASKS:
            return

        try:
            from internagents.frame_service import spawn_bookmarker_background_task

            frame = _frame_from_state(state)
            if frame is None:
                return

            task = await spawn_bookmarker_background_task(
                parent_frame_id=frame["id"],
                root_frame_id=root_frame_id,
                interval_seconds=30,
            )
            _BACKGROUND_BOOKMARKER_TASKS[root_frame_id] = task
            _dbg(f"BOOKMARKER spawned as background task")

        except Exception as e:
            _logger.debug(f"bookmarker spawn failed: {e}")

    def after_model(self, state: dict[str, Any], runtime) -> dict[str, Any] | None:
        """Sync version (for completeness; LangGraph calls aafter_model)."""
        # Diagnostic: confirm whether LangChain dispatches to sync after_model
        print(
            f"🔍 [Verifier] SYNC after_model called "
            f"msgs={len(state.get('messages', []))} "
            f"frame_id={state.get('frame_id', 'none')[:8] if state.get('frame_id') else 'none'}",
            flush=True,
        )
        return None

    async def aafter_model(self, state: dict[str, Any], runtime) -> dict[str, Any] | None:
        """Main hook: checkpoint, review, inject findings, and apply veto gate if needed.

        Veto gate: if frame was transitioning to terminal (completed/pending_user_input)
        and reviewer found issues, revert frame_status to 'running' to force another
        iteration of the main agent.
        """
        # Unconditional entry marker for debugging — remove once verified
        print(
            f"🔍 [Verifier] ENTER aafter_model "
            f"msgs={len(state.get('messages', []))} "
            f"last_idx={state.get('_last_review_msg_idx', 0)} "
            f"frame_id={state.get('frame_id', 'none')[:8] if state.get('frame_id') else 'none'} "
            f"parent={state.get('parent_frame_id')} "
            f"status={state.get('frame_status')}",
            flush=True,
        )
        root_frame_id = state.get("root_frame_id")
        frame_status = state.get("frame_status", "running")

        # Check if we should checkpoint
        if not self._should_checkpoint(state):
            print(f"🔍 [Verifier] SKIP: _should_checkpoint returned False", flush=True)
            return None

        # Check guards
        if self._should_suppress(state):
            print(f"🔍 [Verifier] SKIP: _should_suppress returned True", flush=True)
            return None

        # Run reviewer
        reviewer_frame = await self._run_reviewer(state)

        if reviewer_frame is None:
            # Reviewer failed; increment bounce counter
            if root_frame_id:
                _VERIFICATION_BOUNCES[root_frame_id] = _VERIFICATION_BOUNCES.get(root_frame_id, 0) + 1
            _dbg(f"SUPPRESS (reviewer failed or was bypassed)")
            return None

        # Extract findings
        findings = _extract_findings_from_reviewer(reviewer_frame)

        # Write verdict to JSONL
        if root_frame_id:
            self._write_verdict_jsonl(
                root_frame_id,
                findings,
                at_message_index=len(state.get("messages", [])),
            )

        # Inject findings
        result = self._inject_findings(state, findings)

        # Apply veto gate: if frame was transitioning to terminal status and findings
        # indicate issues, revert frame_status to 'running' to force main to run again
        verdict = findings.get("verdict", "unknown")
        config = self._get_verification_config()
        max_bounces = config.get("max_consecutive_bounces", 3)
        bounce_count = _VERIFICATION_BOUNCES.get(root_frame_id, 0)

        if (
            frame_status in {"completed", "pending_user_input"}
            and verdict != "pass"
            and bounce_count < max_bounces
        ):
            # Veto: revert frame_status to running to force main to run again
            result["frame_status"] = "running"
            _VERIFICATION_BOUNCES[root_frame_id] = bounce_count + 1
            _dbg(
                f"VETO gate: frame_status was '{frame_status}', "
                f"verdict={verdict}, reverting to 'running' "
                f"(bounce {bounce_count + 1}/{max_bounces})"
            )
        elif verdict != "pass":
            _dbg(
                f"NO VETO (frame_status={frame_status}, verdict={verdict}, "
                f"bounce={bounce_count}/{max_bounces})"
            )

        # Maybe spawn bookmarker (fire-and-forget)
        await self._maybe_spawn_bookmarker(state)

        # Cleanup if frame is terminal (and not vetoed back to running)
        if root_frame_id and result.get("frame_status", frame_status) in TERMINAL_FRAME_STATUSES:
            _cleanup_root_frame(root_frame_id)
            _dbg(f"CLEANUP root_frame_id={root_frame_id}")

        return result
