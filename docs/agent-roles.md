# InternAgentS Agent Roles

InternAgentS implements a multi-agent orchestration model with four specialized roles, each tailored for specific tasks in the research workflow. This architecture enables complex research tasks to be broken down into stages, with each stage handled by an agent optimized for that work.

## Overview

| Agent | Trigger | Input | Output | Scope |
|---|---|---|---|---|
| **main** | User starts conversation | User queries + context | Complete solution, artifacts | Full toolkit, unlimited iterations |
| **reviewer** | Main frame completes | Main's messages + output | Structured verdict + issues | Read-only, max 5 iterations |
| **bookmarker** | Main frame completes | Main's messages window | 0-2 quoted key moments | Pattern-match only, no compute |
| **onboarding** | New user / `/start` | Empty (interview-driven) | Task proposal + setup | Interview + setup, no execution |

## Agent Roles in Detail

### main — Primary Agent (Full Capability)

The primary agent that executes user requests end-to-end.

**Responsibilities:**
- Understand user intent and decompose complex research tasks
- Execute computations, fetch data, run analyses
- Create and save artifacts (figures, reports, datasets)
- Spawn child frames for specialized agents (reviewer, bookmarker)

**Capabilities:**
- All tools available (read/write files, run code, web search, MCP access)
- Full skill catalog (loaded dynamically based on task)
- Unrestricted iterations (continue as long as needed)
- Can delegate to other agents

**Configuration:**
- Middlewares: `date`, `goal`, `skill`, `kb_sync`
- Max iterations: unlimited
- Can spawn children: ✓
- Tool whitelist: None (all tools)

**When to use:** Always — this is the user-facing agent for active research.

**Example:**
```
User: "Analyze this dataset and compare gene expression across conditions"
→ main agent loads data, runs statistical tests, creates visualizations
→ Completes with saved artifacts
```

---

### reviewer — Verification Agent (Read-Only)

A post-hoc reviewer that examines the main agent's work for correctness and hallucination.

**Responsibilities:**
- Trace all numerical claims in the main agent's output
- Check for contradictions between prose and execution logs
- Verify artifact contents match what was claimed
- Report fabrication, hallucination, or plan deviations
- Output structured findings (verdict, issues, suggestions)

**Capabilities:**
- Read-only tools: `read_file`, `repl` (no computation)
- Cannot write, edit, or create artifacts
- Fixed analysis job: trace → report (no exploration)
- Structured output (JSON schema with verdict/issues/suggestions)

**Configuration:**
- Middlewares: `date` only
- Max iterations: 5
- Can spawn children: ✗
- Tool whitelist: `read_file`, `repl`
- Tool blacklist: `python`, `bash`, `r`, `save_artifacts`, `edit_file`
- Output schema:
  ```json
  {
    "verdict": "pass" | "fail" | "warn",
    "issues": ["issue 1", "issue 2", ...],
    "suggestions": ["suggestion 1", ...]
  }
  ```

**When to use:** After main frame completes, automatically or manually triggered via `spawn_reviewer()`.

**Example:**
```
Main agent claims: "p-value = 0.0032"
→ Reviewer finds the cell output: t = 3.8, p = 0.0031
→ Reports: verdict="pass", issues=[]

Main agent claims: "Used Bonferroni correction (α=0.05)"
→ Reviewer finds code: alpha = 0.05 / n (already corrected)
→ Reports: verdict="warn", issues=["double-corrected alpha"]
```

---

### bookmarker — Highlight Agent (Pattern-Match)

Extracts 0-2 key moments from a transcript for navigation in future sessions.

**Responsibilities:**
- Identify the landed result or key decision
- Find the statement of what was delivered
- Extract important corrections or pivots
- Return verbatim quotes (for anchor-ability)

**Capabilities:**
- Pattern-match only (no computation or file reads)
- Output: 0-2 quoted spans with labels
- Single-pass analysis (no iteration needed)
- Must preserve exact formatting and spacing

**Configuration:**
- Middlewares: `date` only
- Max iterations: 10 (for complex sessions)
- Can spawn children: ✗
- Tool whitelist: (none — output-only via submit_output)
- Concurrent with parent: ✓ (v0.2; sequential for now)

**Sequential execution (current):**
```
main_frame completes → spawn_child_frame(agent_name="bookmarker")
Bookmarker runs, returns bookmarks → saved to transcript annotations
```

**Concurrent execution (v0.2):**
```
main_frame runs → bookmarker runs in parallel as background task
Both complete independently, results merged
```

**When to use:** Automatically or via `spawn_child_frame(..., agent_name="bookmarker", ...)`. Useful for building session navigation.

**Example output:**
```json
[
  {
    "quote": "**VERDICT:** The metabolic rate increases 2.3-fold under heat stress.",
    "label": "Main finding",
    "msg_idx": 47
  },
  {
    "quote": "[analysis.pdf](artifact:v_123) — statistical methods and raw data",
    "label": "Deliverable location",
    "msg_idx": 49
  }
]
```

---

### onboarding — Setup Agent (Interview-Driven)

Guides new users through an initial setup conversation to configure their workspace and propose a first task.

**Responsibilities:**
- Greet the new user
- Run a short interview (4 questions max) to understand their research
- Solicit optional files/context ("anything else I should know?")
- Propose three concrete first tasks (quick win / hands-on / ambitious)
- Request permissions (web access, skills/connectors)
- Hand off to main agent with the chosen task

**Capabilities:**
- `ask_user` (core interaction mechanism — renders option cards)
- Minimal tools (no execution, no code, no file I/O)
- Warm, conversational tone
- Non-technical UX (pre-rendered questions, clean cards)

**Configuration:**
- Middlewares: `date` only
- Max iterations: 8
- Can spawn children: ✗
- Tool whitelist: `ask_user` primarily
- Tool blacklist: `bash`, `python`, `r`, `repl`, `save_artifacts`, `write_file`, `edit_file`

**When to use:** Automatically on first user session, or manually via `spawn_onboarding()`.

**Flow:**
```
1. User opens InternAgentS
2. Onboarding greets and starts interview
3. Question 1: "What kind of biology do you do?" (pre-rendered)
4. Question 2-4: Adapt to answers (e.g., computational → show relevant tasks)
5. "Anything else I should know?" (open door for context)
6. Propose 3 tasks (quick/hands-on/ambitious)
7. Permissions (Web access / Connectors)
8. "Starting [task] now — main agent takes over"
→ Main agent begins working
```

---

## Registry and Configuration

### Central Registry

All agent configurations are in `internagents/agent_registry.py`:

```python
from internagents.agent_registry import get_agent_config, load_agent_prompt

# Get config for any agent
cfg = get_agent_config("main")
cfg.middlewares  # ("date", "goal", "skill", "kb_sync")
cfg.can_spawn_children  # True

# Load system prompt
prompt = load_agent_prompt("reviewer")
```

### Prompt Files

Each agent's system prompt is stored as a YAML file in `internagents/prompts/agents/`:

```
internagents/prompts/agents/
├── main.yaml           # 2000+ words, adapted from Claude Science's Operon
├── reviewer.yaml       # 4500+ words, verification rubric & heuristics
├── bookmarker.yaml     # 1000+ words, span selection logic
└── onboarding.yaml     # 3000+ words, interview flow & task proposals
```

**YAML Structure:**
```yaml
---
agent_name: MAIN
description: General-purpose scientific computing agent
enable_subtask_delegation: false
# other metadata fields...
---
You are InternAgentS, a general-purpose scientific computing agent...
[system prompt body continues...]
```

### Spawning Agents

**Main agent (root frame):**
```python
from internagents.frame_service import create_and_run_root_frame

frame = await create_and_run_root_frame(
    graph=agent_graph,
    input_data={"query": "analyze X"},
    agent_name="main",  # default
)
```

**Reviewer (child frame):**
```python
from internagents.frame_service import spawn_reviewer

review_frame = await spawn_reviewer(
    parent=main_frame,
    graph=reviewer_graph,
    review_target=main_frame.get("output_data"),
)
```

**Onboarding (root frame):**
```python
from internagents.frame_service import spawn_onboarding

onboarding_frame = await spawn_onboarding(
    graph=onboarding_graph,
    user_id="user@example.com",
)
```

---

## Backward Compatibility

**No agent_name specified?** Default to `main`:
```python
# These are equivalent:
frame = await create_and_run_root_frame(
    graph=graph,
    input_data={"query": "hello"},
    # agent_name omitted → defaults to "main"
)

frame = await create_and_run_root_frame(
    graph=graph,
    input_data={"query": "hello"},
    agent_name="main",  # explicit
)
```

All existing code that doesn't specify `agent_name` continues to work as before.

---

## Known Limitations & Future Work

### Bookmarker Concurrent Invocation (v0.2)

Currently, the bookmarker runs **sequentially as a child frame** after the main frame completes. This is fully functional but not optimized:

```
main_frame: 0-100ms ┐
                    ├→ completed
bookmarker: 0-50ms  ┘
total latency: 100-150ms (sequential)
```

**v0.2 Plan:** Run bookmarker in parallel with main (background task):

```
main_frame: 0-100ms ┐
bookmarker: 0-50ms  ┤ (concurrent)
                    ┘
total latency: ~100ms (overlap)
```

The config flag `concurrent_with_parent` is set to `True` and the spawn mechanism is ready; only the execution strategy in the graph needs implementation.

---

## Design Decisions

1. **Separate agents, not roles.** Each agent has its own system prompt, tool set, and iteration budget. This keeps the LLM's attention focused and makes tool exclusions explicit.

2. **Prompt files, not code.** Agent prompts are data (YAML files), not Python strings. This makes them easier to version, reuse, and adapt.

3. **Backward-compatible defaults.** Frames without an explicit `agent_name` default to `main`. Existing code continues to work unchanged.

4. **Reviewer is read-only by design.** Trace, don't recompute. The reviewer cannot modify the session, only audit it. This preserves the auditability chain.

5. **Onboarding is interview-driven.** Uses `ask_user` cards (structured UI) rather than free-form questions, keeping the interaction tight and guiding toward actionable tasks.

---

## References

- **Agent Registry API**: `internagents/agent_registry.py`
- **Frame Service API**: `internagents/frame_service.py`
- **Frame State**: `internagents/frame_state.py`
- **Agent Graph**: `internagents/agent_graph.py`
- **Tests**: `tests/agent_registry_smoke.py`
