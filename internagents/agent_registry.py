"""Agent registry for 天玄·千枢科学发现平台 multi-agent orchestration.

Each agent (main, reviewer, bookmarker, onboarding) has a configuration that
describes:
- Which prompt YAML to load (system prompt body)
- Which model tier to use (or None = inherit from deepagent.config.json)
- Which tools to expose (whitelist, or None = all)
- Which middlewares to activate
- Runtime budgets and constraints (iterations, concurrent execution)
- Output schema (for structured outputs like reviewer verdicts)

The registry is used by agent_graph.py to route on frame.agent_name and
by frame_service.py to customize agent behavior at spawn time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from internagents.frame_state import AgentName


_PROMPTS_DIR = Path(__file__).parent / "prompts" / "agents"


# YAML loader helpers
def _parse_yaml_metadata(content: str) -> dict[str, Any]:
    """Extract YAML metadata (frontmatter) from a file.

    Tries to use PyYAML if available, falls back to simple regex parsing.
    Handles both --- delimited and pure YAML formats.

    Returns:
        Dict of YAML fields (agent_name, description, etc.)
    """
    # Try PyYAML first
    try:
        import yaml
        # Load only the YAML part (may have embedded markdown body)
        # For our format, metadata is the YAML at the top
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            return data
        return {}
    except ImportError:
        pass

    # Fallback: simple regex-based YAML key:value parser
    result = {}

    # Remove --- delimiters if present
    text = content.lstrip()
    if text.startswith("---\n"):
        text = text[4:]
    if "\n---\n" in text:
        text = text.split("\n---\n", 1)[0]

    # Parse key: value lines at the start
    for line in text.split("\n"):
        line_stripped = line.strip()

        # Stop at first non-YAML-header line (comments, empty, or pipe marker)
        if not line_stripped or line_stripped.startswith("#"):
            continue
        if ": " not in line_stripped and not line.startswith("  "):
            break  # Non-YAML content reached

        if ": " in line_stripped:
            key, value = line_stripped.split(": ", 1)
            key = key.strip()
            value = value.strip()

            # Handle basic types
            if value.lower() in ("true", "false"):
                result[key] = value.lower() == "true"
            elif value.isdigit():
                result[key] = int(value)
            else:
                # Keep as string, may be quoted/formatted
                result[key] = value

    return result


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for one agent role.

    Attributes:
        name: Agent name (main, reviewer, bookmarker, onboarding)
        prompt_path: Path to YAML file with system prompt
        model_override: Model to use, or None for global default
        tool_whitelist: If set, only these tools are available
        tool_blacklist: Tools to exclude
        middlewares: Middleware names to activate
        max_iterations: Max iterations (None = unlimited)
        can_spawn_children: Whether this agent can spawn sub-agents
        concurrent_with_parent: Whether to run concurrently with parent (v0.2)
        output_schema: JSON schema for structured output
        description: Human-readable description
    """
    name: AgentName
    prompt_path: Path
    model_override: str | None = None
    tool_whitelist: tuple[str, ...] | None = None
    tool_blacklist: tuple[str, ...] = ()
    middlewares: tuple[str, ...] = ()
    max_iterations: int | None = None
    can_spawn_children: bool = True
    concurrent_with_parent: bool = False
    output_schema: dict[str, Any] | None = None
    description: str = ""


# Central registry of all agent configurations
AGENT_CONFIGS: dict[AgentName, AgentConfig] = {
    "main": AgentConfig(
        name="main",
        prompt_path=_PROMPTS_DIR / "main.yaml",
        model_override=None,  # Use global default
        tool_whitelist=None,  # All tools available
        tool_blacklist=(),
        middlewares=("date", "frame", "skill", "kb_sync"),
        max_iterations=None,  # Unlimited
        can_spawn_children=True,
        concurrent_with_parent=False,
        description="Primary user-facing agent — full tool access, spawns reviewer/bookmarker as needed",
    ),
    "reviewer": AgentConfig(
        name="reviewer",
        prompt_path=_PROMPTS_DIR / "reviewer.yaml",
        model_override=None,  # Can use smaller model for cost savings
        tool_whitelist=("read_file", "repl"),  # Read-only operations
        tool_blacklist=("python", "bash", "r", "save_artifacts", "edit_file"),
        middlewares=("date",),
        max_iterations=5,  # Matches Claude Science's reviewer_operon_budget
        can_spawn_children=False,  # Reviewers don't spawn children
        concurrent_with_parent=False,
        output_schema={
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["pass", "fail", "warn"]},
                "issues": {"type": "array", "items": {"type": "string"}},
                "suggestions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["verdict"],
        },
        description="Post-hoc reviewer of a completed main frame — verifies results, checks for hallucinations",
    ),
    "bookmarker": AgentConfig(
        name="bookmarker",
        prompt_path=_PROMPTS_DIR / "bookmarker.yaml",
        model_override=None,
        tool_whitelist=(),  # Extremely limited — mostly just output formatting
        tool_blacklist=(
            "python", "bash", "r", "repl", "read_file", "save_artifacts",
            "edit_file", "manage_environments", "manage_packages"
        ),
        middlewares=("date",),
        max_iterations=10,
        can_spawn_children=False,
        concurrent_with_parent=True,  # TODO: implement concurrent invocation in v0.2
        description="Extract bookmarks/key-points from main frame — sequential for now, concurrent in v0.2",
    ),
    "onboarding": AgentConfig(
        name="onboarding",
        prompt_path=_PROMPTS_DIR / "onboarding.yaml",
        model_override=None,
        tool_whitelist=("ask_user",),  # Primarily uses ask_user for interaction
        tool_blacklist=(
            "bash", "python", "r", "repl", "save_artifacts", "manage_environments",
            "manage_packages", "write_file", "edit_file", "fetch_article_fulltext",
            "web_search", "web_fetch", "list_compute"
        ),
        middlewares=("date",),
        max_iterations=8,
        can_spawn_children=False,
        concurrent_with_parent=False,
        description="First-run onboarding — one-shot advisory frame for new users",
    ),
}


class AgentNotFoundError(ValueError):
    """Raised when an agent name is not registered."""


def get_agent_config(name: AgentName) -> AgentConfig:
    """Get the configuration for an agent by name.

    Args:
        name: Agent name (main, reviewer, bookmarker, onboarding)

    Returns:
        The AgentConfig for that agent

    Raises:
        AgentNotFoundError: If the agent is not registered
    """
    if name not in AGENT_CONFIGS:
        raise AgentNotFoundError(f"unknown agent: {name}")
    return AGENT_CONFIGS[name]


def load_agent_prompt(name: AgentName) -> str:
    """Load the agent's system prompt from its YAML file.

    Extracts the prompt body from the YAML. Different agents store the prompt
    in different fields:
    - main: combined identity_prompt + working_style_prompt
    - reviewer: system_prompt
    - bookmarker: system_prompt
    - onboarding: system_prompt

    Args:
        name: Agent name

    Returns:
        System prompt text

    Raises:
        FileNotFoundError: If the prompt file doesn't exist
        AgentNotFoundError: If the agent is not registered
    """
    config = get_agent_config(name)
    if not config.prompt_path.exists():
        raise FileNotFoundError(f"agent prompt not found: {config.prompt_path}")

    with open(config.prompt_path, encoding="utf-8") as f:
        content = f.read()

    # Try to parse as YAML to extract prompt fields
    try:
        import yaml
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            # Check for system_prompt first
            if "system_prompt" in data:
                return str(data["system_prompt"]).strip()
            # For main agent, combine identity + working_style
            if "identity_prompt" in data and "working_style_prompt" in data:
                identity = str(data["identity_prompt"]).strip()
                working = str(data["working_style_prompt"]).strip()
                return f"{identity}\n\n{working}"
            # Fallback: return all YAML string fields joined
            parts = []
            for key in data:
                if isinstance(data[key], str) and not key.startswith("agent_"):
                    parts.append(str(data[key]).strip())
            if parts:
                return "\n\n".join(parts)
    except (ImportError, Exception):
        pass

    # Fallback: extract all multi-line string content after YAML headers
    lines = content.split("\n")
    in_body = False
    body_lines = []

    for line in lines:
        # Skip YAML header lines until we hit a multi-line value marker (|) or body content
        if not in_body:
            if ": |" in line or (line.strip() and not ": " in line and line[0] not in " \t#-"):
                in_body = True
                # If this line has |, skip it and start collecting from next line
                if ": |" in line:
                    continue
            elif line.strip().startswith("system_prompt:") or line.strip().startswith("identity_prompt:"):
                in_body = True
                continue
            else:
                continue

        # Collect body lines
        if in_body:
            body_lines.append(line.rstrip())

    # Remove leading empty lines and rejoin
    while body_lines and not body_lines[0].strip():
        body_lines.pop(0)

    return "\n".join(body_lines).strip()


def load_agent_metadata(name: AgentName) -> dict[str, Any]:
    """Load the YAML metadata from an agent's prompt file.

    Extracts YAML header fields (agent_name, description, etc.) without
    loading the full prompt body.

    Args:
        name: Agent name

    Returns:
        Dict of metadata fields

    Raises:
        AgentNotFoundError: If the agent is not registered
    """
    config = get_agent_config(name)
    with open(config.prompt_path, encoding="utf-8") as f:
        content = f.read()

    return _parse_yaml_metadata(content)


def list_registered_agents() -> list[AgentName]:
    """List all registered agent names.

    Returns:
        List of agent names in registration order
    """
    return list(AGENT_CONFIGS.keys())
