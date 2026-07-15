"""Onboarding gate middleware for the main agent.

Wires the existing onboarding agent + prompt into the runtime: the first
user message in a fresh workspace triggers a one-shot onboarding response
(greeting + optional ask_user card) instead of going to main. Subsequent
turns in the same workspace go straight to main.

v1 semantics: single-turn (one-shot greeting), workspace marked as
onboarded immediately after the first onboarding invocation returns. To
support true multi-turn onboarding later, teach the onboarding LLM to
call `update_frame` with status="completed" when done, then extend this
gate to loop until that signal.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage

from internagents.internagent_resources import ResourceConfig

_logger = logging.getLogger(__name__)


@dataclass
class OnboardingGateMiddleware(AgentMiddleware):
    """Route the first message of an un-onboarded workspace to the onboarding agent.

    Attaches to `main`. On `before_agent`:
      - If workspace has already been onboarded → pass through
      - If not, and the current state has no AIMessage yet (turn 1) →
        invoke the onboarding compiled graph, splice its new messages
        into state, mark workspace as onboarded, and jump to end (main
        does not run this turn)
      - Any error → log, pass through (never block main)
    """

    resource: ResourceConfig

    @property
    def name(self) -> str:
        return f"OnboardingGateMiddleware_{self.resource.id}"

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        # Deferred imports to avoid circular deps at module load time.
        from internagents.agent_graph import get_agent_graph
        from internagents.frame_service import (
            _load_agent_config,
            _save_agent_config,
            _workspace_id_from_resource,
        )

        try:
            workspace_id = _workspace_id_from_resource(self.resource.id)
        except Exception as e:
            _logger.debug(f"OnboardingGate: workspace_id lookup failed: {e}")
            return None

        try:
            config = _load_agent_config()
        except Exception as e:
            _logger.debug(f"OnboardingGate: config load failed: {e}")
            return None

        already_onboarded = (
            config.get("onboarding_completed_workspaces", {}).get(workspace_id, False)
        )
        if already_onboarded:
            return None

        messages = state.get("messages") or []
        has_ai = any(isinstance(m, AIMessage) for m in messages)
        if has_ai or not messages:
            # Either mid-conversation (not turn 1) or empty state — pass through.
            return None

        try:
            onboarding_graph = get_agent_graph(self.resource.id, "onboarding")
        except Exception as e:
            _logger.exception(f"OnboardingGate: failed to resolve onboarding graph: {e}")
            return None

        initial_state = {"messages": list(messages)}
        invoke_config = {
            "configurable": {"thread_id": f"onboarding_gate_{uuid.uuid4().hex[:8]}"}
        }

        try:
            result = await onboarding_graph.ainvoke(initial_state, config=invoke_config)
        except Exception as e:
            _logger.exception(f"OnboardingGate: onboarding invocation failed: {e}")
            return None

        if not isinstance(result, dict):
            _logger.warning(
                f"OnboardingGate: onboarding returned non-dict result "
                f"({type(result).__name__}) — passing through to main"
            )
            return None

        # Splice only the messages onboarding produced this turn.
        original_count = len(messages)
        result_messages = result.get("messages") or []
        new_msgs = list(result_messages[original_count:])

        if not new_msgs:
            _logger.info(
                "OnboardingGate: onboarding produced no new messages — "
                "passing through to main"
            )
            return None

        # Mark workspace as onboarded (v1: one-shot; see module docstring).
        try:
            if "onboarding_completed_workspaces" not in config:
                config["onboarding_completed_workspaces"] = {}
            config["onboarding_completed_workspaces"][workspace_id] = True
            _save_agent_config(config)
            _logger.info(f"OnboardingGate: marked workspace {workspace_id[:8]} as onboarded")
        except Exception as e:
            _logger.warning(f"OnboardingGate: failed to persist onboarded flag: {e}")
            # Continue anyway — user still sees onboarding response this turn.

        return {"jump_to": "end", "messages": new_msgs}
