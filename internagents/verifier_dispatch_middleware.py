"""Verification gate middleware for 天玄·千枢科学发现平台.

VerifierDispatchMiddleware implements synchronous review gating: at each
turn boundary (LLM produced a final AIMessage with no tool_calls, OR LLM
explicitly transitioned frame to completed via update_frame), it blocks
execution, spawns a reviewer child frame, and writes the findings to a
dedicated `reviews` state channel — NEVER to `state.messages`.

Key design (mirrors CS's `verification_checks` table + `verification_update`
WebSocket event pattern):
- Reviewer output goes to `state.reviews` (an independent list state key
  with append-with-id-merge reducer), NOT to `state.messages`. This is the
  critical isolation that prevents review content from ever re-entering
  the LLM's input, avoiding loop and context pollution.
- Synchronous (blocking) gate: main's model call awaits reviewer before
  returning, so veto can happen before user sees the response.
- Veto: only on `verdict=fail` (per reviewer.yaml's rubric — `warn` is
  informational). Veto is a `frame_status="running"` state flag, NOT a
  message the LLM can read.
- Bounce limit: consecutive reviewer failures skip gating after N failures
  (config `max_consecutive_bounces`, default 3).
- Bookmarker fire-and-forget: optional background task for keyword extraction.
- JSONL logging: verdict summary written to
  ~/.internagents/verdicts/<root_frame_id>.jsonl (independent of state).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


def _review_reducer(
    left: list[dict[str, Any]] | None,
    right: list[dict[str, Any]] | dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Append-with-id-merge reducer for the `reviews` state channel.

    Right-side entries are appended. If an entry carries an `id` that
    matches an existing left entry, it REPLACES that entry (reserved for
    future streaming/placeholder patterns).
    """
    if right is None:
        return list(left or [])
    if not isinstance(right, list):
        right = [right]
    merged = list(left or [])
    idx_by_id = {r.get("id"): i for i, r in enumerate(merged) if isinstance(r, dict) and r.get("id")}
    for entry in right:
        if not isinstance(entry, dict):
            continue
        eid = entry.get("id")
        if eid and eid in idx_by_id:
            merged[idx_by_id[eid]] = entry
        else:
            merged.append(entry)
            if eid:
                idx_by_id[eid] = len(merged) - 1
    return merged


class ReviewState(AgentState):
    """State schema owned by VerifierDispatchMiddleware.

    Declaring this on `state_schema` is what registers `reviews` as a real
    LangGraph channel — otherwise the middleware's writes to `state.reviews`
    are silently discarded.
    """

    reviews: NotRequired[Annotated[list[dict[str, Any]], _review_reducer]]

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

# Pre-create verdicts dir at module import time (startup phase, before
# LangGraph's blockbuster is active) so subsequent JSONL writes don't need
# a sync mkdir inside async request handlers.
_VERDICTS_DIR = Path.home() / ".internagents" / "verdicts"
try:
    _VERDICTS_DIR.mkdir(parents=True, exist_ok=True)
except OSError as _e:
    _logger.warning(f"failed to pre-create verdicts dir {_VERDICTS_DIR}: {_e}")


def _cleanup_root_frame(root_frame_id: str) -> None:
    """Clean up module-level state for a root frame."""
    _VERIFICATION_BOUNCES.pop(root_frame_id, None)
    task = _BACKGROUND_BOOKMARKER_TASKS.pop(root_frame_id, None)
    if task is not None:
        task.cancel()


def _msg_role(msg: Any) -> str:
    """Human-readable role label for a message (for review transcripts)."""
    if isinstance(msg, HumanMessage):
        return "User"
    if isinstance(msg, AIMessage):
        return "Assistant"
    if isinstance(msg, SystemMessage):
        return "System"
    return getattr(msg, "type", type(msg).__name__)


def _msg_content_snippet(msg: Any, max_chars: int = 800) -> str:
    """Extract a text snippet from a message, handling multimodal content."""
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        s = content
    elif isinstance(content, list):
        s = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )
    else:
        s = str(content)
    if len(s) > max_chars:
        return s[:max_chars] + "..."
    return s


def _format_review_prompt(messages: list, objective: str) -> str:
    """Build the initial HumanMessage the reviewer LLM will see.

    Without messages in state.messages, the reviewer LangGraph agent has
    no input and terminates immediately. This function renders the parent
    agent's transcript into a text block plus a structured-output directive.
    """
    lines = ["Review the following transcript from the main agent.", ""]
    if objective:
        lines.append(f"Objective: {objective}")
        lines.append("")
    lines.append(f"Transcript ({len(messages)} messages):")
    lines.append("")
    for i, msg in enumerate(messages):
        role = _msg_role(msg)
        content = _msg_content_snippet(msg)
        lines.append(f"[{i + 1}] [{role}] {content}")
    lines.append("")
    lines.append(
        'Report findings as a single JSON object on its own line with keys: '
        'verdict ("pass" | "fail" | "warn"), '
        'issues (list of strings), suggestions (list of strings).'
    )
    lines.append(
        "Trace claims to the transcript above — a value that appears "
        "fabricated or a plan deviation is a valid issue. A value you "
        "cannot trace inside this window is NOT a finding."
    )
    return "\n".join(lines)




# Reviewer output extraction — the reviewer LLM is prompted to emit JSON
# matching `{"verdict":..., "issues":[...], "suggestions":[...]}` but empirically
# it wraps the JSON in ```json ... ``` fences ~60% of the time and emits pure
# prose ~40% of the time (chat-only early exit narrative). These regexes let
# us recover the structured payload in both fenced and embedded-in-prose forms
# before giving up.
_CODE_FENCE_JSON_RE = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)
# Matches a JSON object that contains a "verdict" key. Non-greedy `.*?` with
# DOTALL — we accept nested braces only in string values; if the reviewer
# emits nested objects, JSON parsing will still validate.
_JSON_OBJECT_WITH_VERDICT_RE = re.compile(
    r'\{[^{}]*?"verdict"\s*:\s*"[^"]*"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}',
    re.DOTALL,
)


def _try_parse_reviewer_json(content: str) -> dict[str, Any] | None:
    """Best-effort extract reviewer JSON payload from a string.

    Layered attempts (fast to slow):
      1. Direct `json.loads` on the whole string (happy path when reviewer
         complied and emitted bare JSON)
      2. Strip a ```json ... ``` code fence and parse the inner block
      3. Regex for a `{...}` object containing `"verdict"` embedded in prose

    Returns the parsed dict (with a `verdict` key) on success, or None if
    no valid structured payload was found.
    """
    s = content.strip()
    if not s:
        return None

    # Pattern 1: raw JSON object
    if s.startswith("{"):
        try:
            data = json.loads(s)
            if isinstance(data, dict) and "verdict" in data:
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    # Pattern 2: fenced ```json ... ``` block (or plain ``` ... ```)
    for m in _CODE_FENCE_JSON_RE.finditer(s):
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict) and "verdict" in data:
                return data
        except (json.JSONDecodeError, ValueError):
            continue

    # Pattern 3: JSON object with "verdict" embedded anywhere in prose
    for m in _JSON_OBJECT_WITH_VERDICT_RE.finditer(s):
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict) and "verdict" in data:
                return data
        except (json.JSONDecodeError, ValueError):
            continue

    return None


def _extract_findings_from_reviewer(reviewer_frame: dict[str, Any]) -> dict[str, Any]:
    """Extract structured findings from reviewer frame output.

    Layered extraction (most-authoritative first):
      1. `output_data` dict (populated if a future submit_output tool writes it)
      2. `structured_response` dict (populated if response_format is ever wired)
      3. Last AIMessage content via `_try_parse_reviewer_json` — handles
         raw JSON, ```json ... ``` fenced blocks, and JSON objects embedded
         in prose.

    When no structured output is recoverable, verdict stays `unknown` and
    the raw content (full length, NOT truncated) is stashed under
    `_parse_error` for the caller to move into entry.error. This avoids
    the previous behavior of forcing verdict=warn with a 200-char slice
    of prose in `issues` — a pattern that misclassified pass narratives
    as issues and rendered mid-sentence garbage in the UI.

    Returns dict with keys: verdict, issues, suggestions, and optionally
    `_parse_error` (removed before the entry hits state/JSONL).
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

    # Try structured_response (populated when response_format is used)
    structured = reviewer_frame.get("structured_response")
    if isinstance(structured, dict) and "verdict" in structured:
        findings["verdict"] = structured.get("verdict", "unknown")
        findings["issues"] = structured.get("issues") or []
        findings["suggestions"] = structured.get("suggestions") or []
        if findings["verdict"] != "unknown":
            return findings

    # Try last AIMessage content as JSON — handles fenced blocks and
    # JSON embedded in prose narratives.
    messages = reviewer_frame.get("messages", [])
    if messages:
        last_msg = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if last_msg is not None:
            raw = last_msg.content
            if isinstance(raw, str):
                content_str = raw
            elif isinstance(raw, list):
                # Multimodal content — concatenate text blocks
                content_str = "".join(
                    b.get("text", "") for b in raw if isinstance(b, dict)
                )
            else:
                content_str = str(raw)

            parsed = _try_parse_reviewer_json(content_str)
            if parsed is not None:
                findings["verdict"] = parsed.get("verdict", "unknown")
                findings["issues"] = parsed.get("issues") or []
                findings["suggestions"] = parsed.get("suggestions") or []
                return findings

            # No structured output recoverable. Do NOT force verdict=warn
            # and do NOT truncate — surface the raw content via
            # `_parse_error` so the caller puts it in entry.error.
            if content_str.strip():
                findings["_parse_error"] = content_str

    return findings


@dataclass
class VerifierDispatchMiddleware(AgentMiddleware):
    """Synchronous review gate for the main agent.

    On turn boundary (frame_status ∈ {completed, pending_user_input} OR
    end-of-turn signal on messages), spawns a reviewer child frame and
    writes its findings to `state.reviews` — an independent state channel
    that the LLM's input never reads. Veto (on verdict=fail) is a
    frame_status flip; nothing about reviewer's output ever enters
    state.messages.
    """

    agent_config_dict: dict[str, Any]

    state_schema = ReviewState

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

    @staticmethod
    def _is_end_of_turn(state: dict[str, Any]) -> bool:
        """True when the most recent message is an AIMessage with no tool_calls.

        LangGraph routes to END exactly when the model emits an AIMessage
        without tool_calls — that's the observable "turn boundary" signal.
        We treat it as equivalent to `frame_status='completed'` for review
        purposes and inject that status in `aafter_model`.
        """
        messages = state.get("messages", [])
        if not messages:
            return False
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return False
        return not getattr(last, "tool_calls", None)

    def _should_checkpoint(self, state: dict[str, Any]) -> bool:
        """Check if checkpoint should trigger.

        Fires when either:
          - LLM explicitly transitioned status via update_frame
            (frame_status ∈ {completed, pending_user_input}), OR
          - End-of-turn detected on the message stream
            (last message is AIMessage with no tool_calls).
        """
        config = self._get_verification_config()

        if not config.get("enabled", True):
            return False

        frame_status = state.get("frame_status", "running")
        if frame_status in {"completed", "pending_user_input"}:
            return True

        if self._is_end_of_turn(state):
            return True

        return False

    def _should_suppress(self, state: dict[str, Any]) -> bool:
        """Check if verification should be suppressed by guards.

        Note: the "child frame" guard was intentionally dropped — under the
        CS-aligned lifecycle (1 conversation = 1 root frame + LLM can spawn
        work subframes), a work child that finishes SHOULD be reviewed at
        its own boundary. Meta children (reviewer/bookmarker/onboarding)
        never run this middleware in the first place because it's wired
        only onto the `main` agent (`agent_graph.py:_filter_middlewares_for_agent`).
        """
        # Guard 1: config disabled
        config = self._get_verification_config()
        if not config.get("enabled", True):
            return True

        # Guard 2: max bounces exceeded
        root_frame_id = state.get("root_frame_id")
        if root_frame_id:
            max_bounces = config.get("max_consecutive_bounces", 3)
            bounces = _VERIFICATION_BOUNCES.get(root_frame_id, 0)
            if bounces >= max_bounces:
                return True

        # Guard 3: no active frame
        frame = _frame_from_state(state)
        if frame is None:
            return True

        return False

    async def _run_reviewer(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Spawn reviewer with parent transcript as HumanMessage input.

        The reviewer LangGraph agent needs `state.messages` to have real input;
        passing metadata in `input_data` alone leaves the LLM with no prompt and
        it terminates immediately. We construct an initial_state that includes
        a HumanMessage rendering the parent's transcript.
        """
        from internagents.agent_graph import get_agent_graph
        from internagents.frame_state import create_child_frame

        frame = _frame_from_state(state)

        if frame is None:
            return None

        try:
            # Build a minimal parent-frame stub so create_child_frame can hang
            # the child off it (records parent_frame_id + shared root_frame_id).
            parent_stub = {
                "id": frame["id"],
                "root_frame_id": frame["root_frame_id"],
                "agent_name": "main",
                "status": "running",
            }
            child = create_child_frame(
                parent_stub,
                "reviewer",
                input_data={
                    "review_target_frame_id": frame["id"],
                    "harness_prompt": True,
                },
            )

            # Render parent transcript for the reviewer LLM
            parent_messages = state.get("messages", [])
            parent_input_data = state.get("input_data") or {}
            objective = str(parent_input_data.get("objective") or "")
            review_prompt = _format_review_prompt(parent_messages, objective)

            # Reviewer initial state — MUST include messages so LLM has input
            initial_state = {
                "frame_id": child["id"],
                "root_frame_id": child["root_frame_id"],
                "parent_frame_id": child.get("parent_frame_id"),
                "agent_name": "reviewer",
                "frame_status": "running",
                "messages": [HumanMessage(content=review_prompt)],
                "input_data": child.get("input_data", {}),
            }

            _dbg(f"CHECKPOINT → await reviewer (parent_msgs={len(parent_messages)})")

            reviewer_graph = get_agent_graph("local", "reviewer")
            invoke_config = {"configurable": {"thread_id": child["id"]}}
            result = await reviewer_graph.ainvoke(initial_state, config=invoke_config)

            _dbg(
                f"REVIEWER done frame_status="
                f"{result.get('frame_status', 'unknown') if isinstance(result, dict) else 'not-dict'} "
                f"messages={len(result.get('messages', [])) if isinstance(result, dict) else 0}"
            )
            return result

        except Exception as e:
            _logger.exception(f"reviewer spawn failed: {e}")
            # Note: caller (aafter_model) owns the bounce counter — we
            # don't increment here to avoid double-counting.
            return None

    def _build_review_entry(
        self,
        state: dict[str, Any],
        review_id: str,
        findings: dict[str, Any] | None,
        *,
        status: str,
        bounce_count: int,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Build a review dict for the `state.reviews` channel.

        This intentionally returns a plain dict, NOT a LangChain message —
        reviews live in an independent state channel with its own reducer;
        the LLM never sees this content.

        `status` values:
          - "done"   : reviewer completed, `findings` populated
          - "failed" : reviewer spawn errored, `error` populated
        """
        entry: dict[str, Any] = {
            "id": review_id,
            "at_message_index": len(state.get("messages", [])),
            "status": status,
            "bounce_count": bounce_count,
            "timestamp": int(time.time()),
        }
        if findings is not None:
            entry["verdict"] = findings.get("verdict", "unknown")
            entry["issues"] = findings.get("issues") or []
            entry["suggestions"] = findings.get("suggestions") or []
        if error is not None:
            entry["error"] = error
        return entry

    def _write_verdict_jsonl(
        self,
        root_frame_id: str,
        findings: dict[str, Any],
        at_message_index: int,
    ) -> None:
        """Write verdict to JSONL file for debugging.

        Note: `_VERDICTS_DIR` is pre-created at module import time so this
        function doesn't need to call `os.mkdir` inside the async request
        handler (which would trip LangGraph's blockbuster).
        """
        try:
            output_file = _VERDICTS_DIR / f"{root_frame_id}.jsonl"

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

    async def aafter_model(self, state: dict[str, Any], runtime) -> dict[str, Any] | None:
        """Main hook: at turn boundary, run reviewer and write findings to
        `state.reviews` (an independent channel from state.messages). Veto
        by flipping frame_status back to 'running' — never by injecting
        messages the LLM could read.
        """
        _dbg(
            f"ENTER aafter_model "
            f"msgs={len(state.get('messages', []))} "
            f"frame_id={state.get('frame_id', 'none')[:8] if state.get('frame_id') else 'none'} "
            f"parent={state.get('parent_frame_id')} "
            f"status={state.get('frame_status')}"
        )
        root_frame_id = state.get("root_frame_id")
        frame_status = state.get("frame_status", "running")

        # Permanent-terminal frames don't need review — just cleanup and exit.
        if frame_status in {"failed", "cancelled", "blocked"}:
            if root_frame_id:
                _cleanup_root_frame(root_frame_id)
                _dbg(f"CLEANUP root_frame_id={root_frame_id} (permanent-terminal)")
            return None

        # Should we run review this turn?
        if not self._should_checkpoint(state):
            _dbg("SKIP: _should_checkpoint returned False")
            return None

        # Guards (config disabled / max bounces / no active frame)
        if self._should_suppress(state):
            _dbg("SKIP: _should_suppress returned True")
            return None

        review_id = f"review_{uuid.uuid4().hex[:8]}"

        # Run reviewer synchronously
        reviewer_frame = await self._run_reviewer(state)

        result: dict[str, Any] = {}

        if reviewer_frame is None:
            # Bump bounces + write a "failed" review entry so the frontend
            # can render "review failed" instead of hanging silent.
            if root_frame_id:
                _VERIFICATION_BOUNCES[root_frame_id] = _VERIFICATION_BOUNCES.get(root_frame_id, 0) + 1
            bounce_count = _VERIFICATION_BOUNCES.get(root_frame_id or "", 0)
            entry = self._build_review_entry(
                state,
                review_id,
                findings=None,
                status="failed",
                bounce_count=bounce_count,
                error="reviewer spawn failed",
            )
            result["reviews"] = [entry]
            _dbg(f"REVIEWER failed — bounce_count={bounce_count}")
            return result

        # Extract findings + persist to JSONL (independent of state)
        findings = _extract_findings_from_reviewer(reviewer_frame)
        # `_parse_error` is a sentinel from the extractor when reviewer
        # output was unparseable — surface it as entry.error and drop from
        # findings so it doesn't pollute state or JSONL.
        parse_error = findings.pop("_parse_error", None)
        if root_frame_id:
            self._write_verdict_jsonl(
                root_frame_id,
                findings,
                at_message_index=len(state.get("messages", [])),
            )

        bounce_count = _VERIFICATION_BOUNCES.get(root_frame_id or "", 0)
        entry = self._build_review_entry(
            state,
            review_id,
            findings=findings,
            status="done",
            bounce_count=bounce_count,
            error=(
                "Reviewer output could not be parsed as structured JSON. "
                "Raw content:\n\n" + parse_error
            ) if parse_error else None,
        )
        result["reviews"] = [entry]
        _dbg(
            f"RESULT verdict={findings.get('verdict')} "
            f"issues_count={len(findings.get('issues') or [])} "
            f"bounce_count={bounce_count} review_id={review_id}"
        )

        # Veto only on `fail` (per reviewer.yaml rubric: warn=informational).
        # Veto = flip frame_status back to 'running'; no message goes into
        # state.messages, LLM never learns it was vetoed.
        verdict = findings.get("verdict", "unknown")
        config = self._get_verification_config()
        max_bounces = config.get("max_consecutive_bounces", 3)

        end_of_turn = self._is_end_of_turn(state)
        turn_end_signal = (
            frame_status in {"completed", "pending_user_input"} or end_of_turn
        )

        if (
            turn_end_signal
            and verdict == "fail"
            and bounce_count < max_bounces
        ):
            result["frame_status"] = "running"
            _VERIFICATION_BOUNCES[root_frame_id] = bounce_count + 1
            _dbg(
                f"VETO gate: end_of_turn={end_of_turn} "
                f"frame_status={frame_status} verdict={verdict}, "
                f"reverting to 'running' (bounce {bounce_count + 1}/{max_bounces})"
            )
        elif end_of_turn and frame_status not in {"completed", "pending_user_input"}:
            # No veto, end-of-turn: canonicalize to `completed` so
            # FrameRootMiddleware revives on the next user turn.
            result["frame_status"] = "completed"
            _dbg(f"END-OF-TURN → frame_status=completed (verdict={verdict})")
        elif verdict != "pass":
            _dbg(
                f"NO VETO (frame_status={frame_status}, verdict={verdict}, "
                f"bounce={bounce_count}/{max_bounces}) — warn/unknown does not force re-loop"
            )

        # Fire-and-forget bookmarker
        await self._maybe_spawn_bookmarker(state)

        return result
