# Frame Model — Phase 2 Architecture

## Overview

Frame is the fundamental execution unit in InternAgentS Phase 2. It replaces the flat `GoalState` model with a tree-structured state that supports:

- **Parent/child derivation** for multi-agent orchestration (e.g., main agent spawning a reviewer)
- **Session boundaries** via `root_frame_id` (one conversation tree = one root)
- **Independent thread IDs** for each frame (clean checkpointing, no state conflicts)
- **Complete resumability** (messages + context_data = full snapshot)

## Core Concepts

### Frame Structure

```python
class FrameState(TypedDict):
    id: str                          # unique frame UUID
    root_frame_id: str               # session identifier
    parent_frame_id: str | None      # which frame spawned this (None for root)
    agent_name: str                  # "main", "reviewer", "bookmarker", etc.
    status: str                      # "pending", "running", "completed", "failed", "cancelled"
    messages: list[AnyMessage]       # LangGraph message array
    system_prompt: str               # assembled prompt
    input_data: dict                 # raw input
    output_data: dict                # final result
    tokens_used: int                 # cumulative token consumption
    time_used_seconds: int           # elapsed time
    created_at: int                  # unix timestamp
    updated_at: int                  # unix timestamp
    skills_attached: list[str]       # evolution tracking
    mcp_servers_attached: list[str]  # evolution tracking
    evolution_context: dict          # reserved for Phase 3+
```

### Root vs. Child Frames

**Root Frame** (created when user starts a conversation):
- `root_frame_id == id`
- `parent_frame_id = None`
- Agent name is usually "main"

**Child Frame** (spawned from a parent):
- `root_frame_id` inherited from parent (all siblings share the same root)
- `parent_frame_id` points to the parent frame's id
- Different agent (e.g., "reviewer")
- Independent thread_id for checkpointing

Example: In a single user conversation, you might have:
```
Root Frame (id=abc123, agent_name="main") ──┐
                                             ├── Child Frame (id=def456, parent=abc123, agent_name="reviewer")
                                             └── Child Frame (id=ghi789, parent=abc123, agent_name="bookmarker")
```

All three frames have `root_frame_id = abc123`.

## Three Core APIs

### 1. `create_and_run_root_frame()`

Start a new conversation.

```python
from internagents.frame_service import create_and_run_root_frame

frame = await create_and_run_root_frame(
    graph=my_agent_graph,
    input_data={"query": "hello"},
    agent_name="main",
)
# frame["status"] is now "completed", "failed", or "cancelled"
# frame["output_data"] contains the result
```

### 2. `spawn_child_frame()`

Derive a sub-agent from a parent.

```python
from internagents.frame_service import spawn_child_frame

parent = ...  # completed main frame
child = await spawn_child_frame(
    parent=parent,
    graph=reviewer_graph,
    agent_name="reviewer",
    input_data={"target": parent["id"]},
)
# child["parent_frame_id"] == parent["id"]
# child["root_frame_id"] == parent["root_frame_id"]
```

### 3. `resume_frame()`

Restart a paused or interrupted frame.

```python
from internagents.frame_service import resume_frame

resumed = await resume_frame(
    frame_id="abc123",
    graph=my_agent_graph,
    additional_input={"user_approval": True},
    checkpointer=my_checkpointer,
)
```

## LangGraph Integration

Each frame gets its own `thread_id` (set to `frame["id"]`), ensuring:

- Independent checkpoints per frame
- No shared state conflicts between parent and child
- Clean resume/pause semantics per frame

**Config passed to graph.ainvoke():**
```python
config = {
    "configurable": {
        "thread_id": frame["id"]  # key: frame's unique ID
    }
}
result = await graph.ainvoke(frame, config=config)
```

## Backward Compatibility

GoalState and goal_middleware.py continue to work:

- Legacy code that uses GoalState still functions
- New Frame-aware code can coexist
- Helper functions bridge the two models:
  - `frame_from_goal(goal)` → FrameState
  - `goal_from_frame(frame)` → GoalState

Existing middleware (date_middleware, kb_sync_middleware, etc.) is unaffected.

## Migration Path

### Phase 2 (Current)
- Frame foundation (APIs + TypedDict)
- Backward compatibility bridge
- Optional: integrate with existing agent graph

### Phase 3
- Multi-agent system (main, reviewer, bookmarker, onboarding)
- Skills with frame-level attach/detach
- Skill evolution (propose/review/commit)
- Memory recall injected into system prompt

### Phase 4
- Relay integration (cross-machine frame dispatch)
- External Claude Science compatibility layer

## Usage Example: Minimal

```python
import asyncio
from internagents.frame_state import create_root_frame
from internagents.frame_service import create_and_run_root_frame

async def main():
    # Create and run a root frame
    frame = await create_and_run_root_frame(
        graph=my_compiled_graph,
        input_data={"user_query": "What is AI?"},
        agent_name="main",
    )
    
    print(f"Frame {frame['id']} completed with status: {frame['status']}")
    print(f"Output: {frame.get('output_data')}")
    print(f"Tokens used: {frame['tokens_used']}")
    print(f"Time used: {frame['time_used_seconds']}s")

asyncio.run(main())
```

## Testing

Run the smoke tests:

```bash
cd /mnt/shared-storage-user/wangrunsheng-p/OpenClaudeScience
python tests/frame_state_smoke.py
```

These test pure Python data model operations (no LangGraph needed).

## Files

- `internagents/frame_state.py` — FrameState TypedDict + validators + factories
- `internagents/frame_service.py` — create_and_run_root_frame / spawn_child_frame / resume_frame
- `internagents/agent_graph.py` — InternAgentState extended with Frame fields + helper
- `docs/frame-model.md` — this file
- `tests/frame_state_smoke.py` — pure-Python smoke tests
