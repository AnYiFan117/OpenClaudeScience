"""Interaction tools for the main agent.

Provides two tools:

- `ask_user`: raises a LangGraph `interrupt(...)` with a structured payload
  the frontend renders as an `AskUserInterrupt` card. The user's answer
  (either the selected option's value or free-text from the "其他" input)
  is returned to the LLM.

- `mark_workspace_onboarded`: persists the current workspace's onboarding
  status in `deepagent.config.json` by writing
  `onboarding_completed_workspaces[workspace_id] = True`. Called by the
  LLM at the end of the bootstrap conversation.
"""

from __future__ import annotations

from typing import Any

from langchain.tools import tool
from langgraph.types import interrupt


@tool("ask_user")
def ask_user(
    question: str,
    options: list[dict[str, str]] | None = None,
    description: str | None = None,
    allow_other: bool = True,
) -> str:
    """Ask the user a structured question and wait for their answer.

    Use this to collect a preference, category, or short input from the user
    when the LLM cannot decide on its own. The frontend renders a card with
    the question, clickable option buttons, and (if allow_other) an "其他"
    free-text input.

    Args:
        question: Short question shown at the top of the card, e.g. "你主要在做什么？"
        options: List of option objects, each with at least "label" (button text)
            and "value" (the string the tool returns if picked). Optional "description"
            adds a short subtitle under the button. Example:
            [{"label": "科研实验", "value": "science_research"},
             {"label": "代码项目", "value": "code_project"}]
        description: Optional grey subtitle below the question.
        allow_other: If True (default), the card includes a "其他" button that
            expands a textarea; the user's typed string is returned verbatim.

    Returns:
        The user's answer as a string. Either an option `value` or free text.
    """
    payload: dict[str, Any] = {
        "type": "ask_user",
        "question": question,
        "description": description,
        "options": list(options or []),
        "allow_other": bool(allow_other),
    }
    answer = interrupt(payload)
    if isinstance(answer, str):
        return answer
    return str(answer) if answer is not None else ""


@tool("mark_workspace_onboarded")
def mark_workspace_onboarded(resource_id: str = "local") -> str:
    """Persist that the current workspace has completed its first-run onboarding.

    Call this ONCE at the end of the bootstrap conversation so future sessions
    in this workspace skip the onboarding flow. No-op if the workspace was
    already marked.

    Args:
        resource_id: Resource identifier (usually "local"). Defaults to "local".

    Returns:
        A short status string suitable for the LLM to acknowledge.
    """
    from internagents.frame_service import (
        _load_agent_config,
        _save_agent_config,
        _workspace_id_from_resource,
    )

    workspace_id = _workspace_id_from_resource(resource_id)
    config = _load_agent_config()
    completed = config.get("onboarding_completed_workspaces")
    if not isinstance(completed, dict):
        completed = {}
        config["onboarding_completed_workspaces"] = completed

    if completed.get(workspace_id):
        return f"already onboarded ({workspace_id[:8]})"

    completed[workspace_id] = True
    _save_agent_config(config)
    return f"onboarded ({workspace_id[:8]})"


@tool("write_memory")
def write_memory(content: str, resource_id: str = "local") -> str:
    """Persist a concise user-context summary to workspace-scoped memory.

    The content is written as a full-overwrite markdown file at
    `.internagents/user-memory/<workspace_id>.md`, relative to the workspace
    root. Every future run of the main agent in this workspace will see
    this memory as a system-level context injection, so it should read
    like durable notes about the user — their focus area, workflow stage,
    tools, and goals — NOT the transient details of one conversation.

    Call this once per onboarding conversation, right before
    `mark_workspace_onboarded`. Overwrites any previous memory for this
    workspace — pass the full desired content each time.

    Args:
        content: Markdown text summarizing the user's context. Keep it
            short (a handful of bullet points).
        resource_id: Resource identifier (usually "local"). Defaults to "local".

    Returns:
        A short status string suitable for the LLM to acknowledge.
    """
    from pathlib import Path

    from internagents.frame_service import _workspace_id_from_resource

    workspace_id = _workspace_id_from_resource(resource_id)
    memory_dir = Path.cwd() / ".internagents" / "user-memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_file = memory_dir / f"{workspace_id}.md"
    memory_file.write_text(content.strip() + "\n", encoding="utf-8")
    return f"memory saved ({workspace_id[:8]}, {len(content)} chars)"


def interaction_tools() -> list[Any]:
    """Return the LangChain tools this module exports."""
    return [ask_user, mark_workspace_onboarded, write_memory]
