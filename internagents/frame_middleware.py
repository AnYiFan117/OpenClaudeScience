"""Frame state middleware for InternAgentS agent graphs.

Ensures every agent's state has Frame fields properly initialized before each model call.
This middleware acts as a bridge between legacy GoalState-only execution and new Frame-aware execution.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import Interrupt
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest


class FrameEnsureMiddleware(AgentMiddleware):
    """Middleware that ensures Frame state is populated before model calls.

    If state lacks frame_id or other frame fields, this middleware populates them
    from GoalState or generates defaults. This allows graphs to seamlessly handle
    both legacy Goal-only invokes and new Frame-aware invokes.

    Applied to every agent's middleware chain (main, reviewer, bookmarker, onboarding).
    """

    async def before_model_call(self, request: ModelRequest) -> ModelRequest | Interrupt | None:
        """Ensure frame fields are present before the model runs.

        Args:
            request: the ModelRequest being prepared

        Returns:
            Updated ModelRequest with frame fields ensured (or Interrupt if critical error)
        """
        if not hasattr(request, "state") or not isinstance(request.state, dict):
            return request

        state = request.state
        if "frame_id" not in state or not state.get("frame_id"):
            # Frame fields missing; populate from goal or generate defaults
            from internagents.frame_state import create_root_frame

            goal = state.get("goal")
            input_data = {}
            if isinstance(goal, dict) and goal.get("objective"):
                input_data = {"objective": goal["objective"]}

            defaults = create_root_frame(agent_name="main", input_data=input_data)

            # Merge defaults into state, preserving existing fields
            state["frame_id"] = state.get("frame_id") or defaults["id"]
            state["root_frame_id"] = state.get("root_frame_id") or defaults["root_frame_id"]
            state["parent_frame_id"] = state.get("parent_frame_id") or defaults.get("parent_frame_id")
            state["agent_name"] = state.get("agent_name") or defaults["agent_name"]
            state["frame_status"] = state.get("frame_status") or defaults["status"]
            if "tokens_used" not in state:
                state["tokens_used"] = defaults["tokens_used"]
            if "time_used_seconds" not in state:
                state["time_used_seconds"] = defaults["time_used_seconds"]

        # Update request state with ensured frame fields
        request.state = state
        return request

    async def after_model_call(self, response: dict[str, Any]) -> dict[str, Any] | None:
        """Optional: track elapsed time if frame is still running."""
        return response
