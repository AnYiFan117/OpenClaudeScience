# Agent Runtime Routing (Phase 3.5)

## Overview

Phase 3.5 implements **runtime routing** for multi-agent orchestration in OpenClaudeScience. Instead of a single monolithic agent, the system now supports 4 distinct agent roles—**main**, **reviewer**, **bookmarker**, **onboarding**—each with its own:

- **System prompt** (loaded from `internagents/prompts/agents/*.yaml`)
- **Tool whitelist/blacklist** (configurable per agent)
- **Middleware set** (date, goal, skill, kb_sync)
- **Model tier** (optional per-agent override)
- **Execution constraints** (iterations, timeouts)

## Architecture

### Agent Graph Caching

Agent graphs are built **lazily and cached** per `(resource_id, agent_name)` tuple:

```python
# Coordinator mode: get_agent_graph("local", "main")
graph = get_agent_graph("local", "main")    # main agent for local resource
graph = get_agent_graph("local", "reviewer") # reviewer agent for local resource

# Runtime mode: reads INTERNAGENT_RUNTIME_AGENT_NAME env var
# export INTERNAGENT_RUNTIME_AGENT_NAME=reviewer
# python -m your_app  # runs as reviewer agent
```

### Frame Routing

Each frame has an `agent_name` field. The frame service automatically resolves the correct graph:

```python
# Create root frame
frame = await create_and_run_root_frame(
    input_data={"query": "..."},
    agent_name="main",  # resolved to main graph
)

# Spawn reviewer child
reviewer_frame = await spawn_reviewer(
    parent=frame,
    # graph= is optional; auto-resolved from agent_name="reviewer"
)
```

## Configuration

### 1. Agent Configurations (agent_registry.py)

Each agent is defined in `AGENT_CONFIGS`:

```python
from internagents.agent_registry import AGENT_CONFIGS

# Example: reviewer config
reviewer_config = AGENT_CONFIGS["reviewer"]
# - prompt_path: internagents/prompts/agents/reviewer.yaml
# - tool_whitelist: ("read_file", "repl")  # None means all tools
# - tool_blacklist: ("python", "bash", ...)
# - middlewares: ("date",)  # subset of full middleware
# - max_iterations: 5
```

### 2. Agent Prompts (prompts/agents/)

Each agent has a dedicated YAML prompt file:

```
internagents/prompts/agents/
├── main.yaml        # Primary user-facing agent
├── reviewer.yaml    # Post-hoc verification
├── bookmarker.yaml  # Extract key points
└── onboarding.yaml  # First-run setup
```

Prompt files are loaded via `load_agent_prompt(agent_name)` which extracts the `system_prompt` or combined `identity_prompt` + `working_style_prompt` fields.

## Usage

### In Coordinator Mode (default)

```python
from internagents.agent_graph import get_agent_graph
from internagents.frame_service import create_and_run_root_frame, spawn_reviewer

# Get graphs explicitly
main_graph = get_agent_graph("local", "main")
reviewer_graph = get_agent_graph("local", "reviewer")

# Run main frame
main_frame = await create_and_run_root_frame(
    input_data={"query": "analyze this paper"},
    agent_name="main",
)

# Spawn reviewer (graph auto-resolved)
review_frame = await spawn_reviewer(parent=main_frame)
```

### In Runtime Mode

```bash
# Runtime mode with default main agent
export INTERNAGENT_PROCESS_ROLE=runtime
python -m your_app

# Runtime mode with specific agent
export INTERNAGENT_PROCESS_ROLE=runtime
export INTERNAGENT_RUNTIME_AGENT_NAME=reviewer
python -m your_app  # runs only as reviewer
```

## Concurrent Bookmarker (v0.2)

The bookmarker can run **concurrently** with a main frame, periodically snapshotting state and extracting key points:

```python
from internagents.frame_service import create_and_run_root_frame_with_bookmarker

frame = await create_and_run_root_frame_with_bookmarker(
    input_data={"query": "..."},
    enable_bookmarker=True,
    bookmarker_interval=30,  # seconds between checks
    checkpointer=my_checkpointer,
)

# Bookmarks written to: ~/.internagents/bookmarks/{root_frame_id}.jsonl
```

### Bookmarks Output Format

Each line in the JSONL file is a bookmark entry:

```json
{
  "timestamp": 1234567890,
  "parent_frame_id": "abc-123",
  "at_message_index": 42,
  "bookmark": {
    "title": "Key insight from analysis",
    "content": "..."
  }
}
```

### Manual Concurrent Bookmarker

For fine-grained control:

```python
from internagents.frame_service import (
    spawn_bookmarker_background_task,
    create_and_run_root_frame,
)

# Spawn main frame
main_frame = await create_and_run_root_frame(
    input_data={"query": "..."},
    agent_name="main",
)

# Start bookmarker task
bookmarker_task = await spawn_bookmarker_background_task(
    parent_frame_id=main_frame["id"],
    root_frame_id=main_frame["root_frame_id"],
    interval_seconds=30,
    checkpointer=my_checkpointer,
)

# ... main frame continues ...

# Stop bookmarker when done
bookmarker_task.cancel()
try:
    await bookmarker_task
except asyncio.CancelledError:
    pass
```

## Tool and Middleware Filtering

### Tool Filtering

Tools are filtered by agent config's `tool_whitelist` and `tool_blacklist`:

```python
from internagents.agent_graph import _filter_tools_by_agent

# If whitelist is None (main agent): keep all tools except blacklist
# If whitelist is set (reviewer): keep only whitelisted tools, then remove blacklist

# Example: reviewer keeps only read-safe tools
reviewer_tools = _filter_tools_by_agent(all_tools, reviewer_config)
# Result: read_file ✓, write_file ✗, bash ✗
```

### Middleware Filtering

Middlewares are built from agent config's `middlewares` tuple:

```python
from internagents.agent_graph import _filter_middlewares_for_agent

# Supported middleware names: "date", "goal", "skill", "kb_sync"
# main: (date, goal, skill, kb_sync) — full set
# reviewer: (date,) — minimal

middleware = _filter_middlewares_for_agent(agent_config_dict, agent_cfg)
```

## Backward Compatibility

### Module-Level Exports

The existing `agent`, `agent_local`, `agent_remote1–8` exports still work:

```python
from internagents.agent_graph import agent, agent_local

# Both point to the main agent for their resource
# No change needed for existing code
```

### Frame Creation Without agent_name

Frames without an explicit `agent_name` default to `"main"`:

```python
from internagents.frame_state import create_root_frame

# Defaults to "main" agent
frame = create_root_frame(input_data={"query": "..."})
assert frame["agent_name"] == "main"
```

## Cache Invalidation

The agent graph cache is keyed by `(resource_id, agent_name)`. To clear it:

```python
from internagents.agent_graph import _AGENT_GRAPH_CACHE

# Clear everything
_AGENT_GRAPH_CACHE.clear()

# Clear a specific agent
key = ("local", "reviewer")
_AGENT_GRAPH_CACHE.pop(key, None)
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `INTERNAGENT_PROCESS_ROLE` | `"coordinator"` | `"runtime"` for agent runtime mode |
| `INTERNAGENT_RUNTIME_AGENT_NAME` | `"main"` | Agent to run in runtime mode |
| `INTERNAGENT_RUNTIME_ID` | `"runtime"` | Resource ID for runtime agent |

## Testing

Run smoke tests to verify routing and bookmarker:

```bash
# Agent routing tests (no model calls)
python tests/agent_routing_smoke.py

# Bookmarker concurrency tests (no model calls)
python tests/bookmarker_concurrent_smoke.py
```

Both tests verify structure and basic logic without invoking a model.

## Future Enhancements

- **Agent Skill Evolution** (Phase 4): dynamic skill generation per agent
- **Memory Compression** (Phase 4): rolling compact of archived bookmarks
- **Performance Tuning**: parallel reviewer + bookmarker + main in a single frame tree
