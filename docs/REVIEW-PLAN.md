# InternAgentS `feature/frame-agents` Review Plan

> **活的 review 追踪文档**。每 review 一项，在 status 里改标记。等所有 R1-R5 处理完，本文档可以删掉再 merge。

**分支**：`feature/frame-agents`（从 `main` 分出 7 commits）
**当前 head**：`ab950bc`
**测试**：45/45 全绿
**净增量**：18 files / +4489 / -50 行

---

## Legend

| 标记 | 含义 |
|---|---|
| ✅ | 已 review 且已批准 |
| 🟡 | 等你 review |
| 🔴 | 已识别为 bug 或高风险，等你决定要不要修 |
| ⚠️ | 有取舍，需要你决策 |
| 🚧 | 正在做 |
| 📋 | 计划中未开始 |
| ❌ | 已否决 / 不做 |

---

## Part A · 已完成的 7 个 commits

### C1 · `7a02c28` — Phase 1 · Webapp 入口 [🟡]

**改动**：新入口 `scripts/web.sh`（60 行）+ `docs/webapp.md`（141 行）+ README 新加 Web Mode 段落。**未动 electron desktop/**。

**Review 重点**：
- 是不是就是包了一层 `dev.sh`？
- 有没有 --open 走 xdg-open / open

**状态**：🟡 等 review

---

### C2 · `4b8153c` — Phase 2 · Frame as basic unit [🟡]

**新建 Python 文件**：
- `internagents/frame_state.py`（422 行）— FrameState TypedDict + 5 个 factory + validator + goal 兼容桥
- `internagents/frame_service.py`（228 行，Phase 2 部分）— create_and_run_root_frame / spawn_child_frame / resume_frame

**编辑**：
- `internagents/agent_graph.py`：`class InternAgentState` 加 11 个 Frame optional 字段（line 457-475）

**Review 重点**：
- FrameState 字段设计（`id / root_frame_id / parent_frame_id / agent_name / status / messages / tokens_used / time_used_seconds / created_at / updated_at`）
- 独立 thread_id 派生策略（`spawn_child_frame` 用不同 thread_id）
- `goal_from_frame` / `frame_from_goal` 兼容桥的字段映射是否合理

**状态**：🟡 等 review

---

### C3 · `a05bf7a` — Phase 3 · 4 agent 角色 [🟡]

**新建**：
- 4 个 YAML prompts（`internagents/prompts/agents/{main,reviewer,bookmarker,onboarding}.yaml`）— 从 Claude Science 抬升，身份改为 InternAgentS
- `internagents/agent_registry.py`（316 行）— `AgentConfig` dataclass + `AGENT_CONFIGS` dict + YAML loaders

**编辑**：
- `frame_service.py`：新增 `spawn_reviewer` / `spawn_onboarding` 便捷 wrapper

**Review 重点**：
- 4 个 YAML prompt 内容（尤其 main.yaml 的身份适配）
- `AgentConfig` 每个 agent 的 tool_whitelist / middlewares / max_iterations / can_spawn_children 设置

**状态**：🟡 等 review

---

### C4 · `30de85a` — Phase 3.5 · Runtime 路由 + 并发 bookmarker [🟡]

**编辑**：
- `agent_graph.py`：新增 5 个函数（`_AGENT_GRAPH_CACHE`, `_filter_tools_by_agent`, `_filter_middlewares_for_agent`, `_build_agent_graph_for`, `get_agent_graph`）— 约 210 行
- `frame_service.py`：新增 `spawn_bookmarker_background_task` + `create_and_run_root_frame_with_bookmarker` + 辅助函数

**Review 重点**：
- 每 agent 的懒建缓存策略（`_AGENT_GRAPH_CACHE`）
- Bookmarker 并发 task 的 `asyncio.Task` + checkpointer poll + JSONL 落盘

**状态**：🟡 等 review

---

### C5 · `1e4f83a` — Phase 4a · Frame 真源头 [🟡]

**新建**：
- `internagents/frame_middleware.py`（67 行）— `FrameEnsureMiddleware` 保证每次 model call 前 state 里有 frame 字段

**编辑**：
- `internagents/goal_tools.py`：`create_goal` 内部改为 **Frame-first**（先 `create_root_frame`，再 `goal_from_frame` 派生 Goal 视图）。`update_goal` 同步 Frame status。`_command_with_goal` → `_command_with_frame`
- `internagents/frame_state.py`：`create_root_frame` 加 `frame_id` 可选参数（支持 pin 到 LangGraph thread_id）

**Review 重点**：
- `create_goal` 里 Frame-first 逻辑
- `_command_with_frame` 往 state 写 7 个字段（是否与 InternAgentState 定义一致——见 R1）
- `FrameEnsureMiddleware.before_model_call` 签名（见 R3）

**状态**：🟡 等 review

---

### C6 · `4d786b0` — Phase 4b · 消除剩余路由影子 [🟡]

**编辑**：
- `internagents/agent_graph.py`：module 底部 exports 改用 `get_agent_graph("local", "main")`（之前是 `_resource_agents.get("local", agent)`）
- `create_runtime_agent` 也套上 `_filter_tools_by_agent` + `_filter_middlewares_for_agent`

**Review 重点**：
- module 底部 `agent_local` / `agent` 的重定向逻辑
- 未包 try/except（见 R4）

**状态**：🟡 等 review

---

### C7 · `ab950bc` — Cleanup（blocked + dead code + rename）[🟡]

**编辑**：
- `frame_state.py`：`FrameStatus` 加 `"blocked"` 值 + validators 同步
- `goal_tools.py`：`_command_with_goal` → `_command_with_frame`；删除 `create_goal_state` import
- `agent_graph.py`：删除 dead code `_ensure_frame_state`

**保留**：
- `_resolve_resource`（subagent 抓到它在 `_build_agent_graph_for:1606` 有真调用，我之前误判为 dead code）

**新增测试**：`test_blocked_status_valid`

**状态**：🟡 等 review

---

## Part B · 待修的 5 个已识别问题

### R1 · `_command_with_frame` 写入的字段名 vs `InternAgentState` 定义 [🔴 需修]

**问题**：`goal_tools.py:_command_with_frame` 往 state 写 7 个字段：
```
frame_id / root_frame_id / parent_frame_id / agent_name / frame_status /
tokens_used / time_used_seconds
```

而 `agent_graph.py:457-475` 里 `class InternAgentState(TypedDict)` 加的 Frame 字段是（Phase 2 时定的）：
```
id / root_frame_id / parent_frame_id / agent_name / status / tokens_used /
time_used_seconds / input_data / output_data / system_prompt / skills_attached /
mcp_servers_attached
```

**冲突**：`frame_id` vs `id`；`frame_status` vs `status`。同一个 Frame 数据，两处用不同字段名，LangGraph state 里会**分裂成两组**。

**建议**：统一。我推荐用 `frame_id` / `frame_status`（避免和 langgraph 的通用 `status` 冲突），把 InternAgentState 里的 `id` / `status` 改成 `frame_id` / `frame_status`。

**影响文件**：
- `agent_graph.py:457-475`（改 InternAgentState 字段名）
- `frame_middleware.py`（`_ensure_frame_state` 早已删，middleware 用的是 `frame_id` — 正确）
- 各测试文件（如果测的是老字段名）

**状态**：🔴 未修，等你确认命名方案

---

### R2 · `update_goal` 里 `hasattr(runtime, "state")` 探测 [🟡 需验证]

**问题**：`goal_tools.py:153` 用 `hasattr(runtime, "state") and isinstance(runtime.state, dict)` 判断能不能读 state。但 **LangChain `ToolRuntime` API 是否真有 `.state` 属性未验证**。如果没有，这段代码永远走不进 if 分支——**Frame 状态永远不会被同步**（silent failure）。

**验证方法**：
```python
# 在 update_goal 里加一行 log
import langchain.tools; print(type(runtime).__mro__)
# 或 grep langchain 源码
```

**建议**：
- 优先：改用官方文档的方式读 state（如果 ToolRuntime 提供别的接口）
- 保底：加个 `else` 分支 log warning，让不同步的行为显式化，不静默

**影响文件**：`goal_tools.py:150-170`

**状态**：🟡 待验证 API 后决定

---

### R3 · `FrameEnsureMiddleware.before_model_call` 返回类型 [🟡 需验证]

**问题**：`frame_middleware.py:29` 的 `before_model_call` 返回 `ModelRequest | Interrupt | None`。但对照 InternAgentS 里其他 middleware（`date_middleware.py`, `goal_middleware.py`），可能**惯用返回 None**（就地修改 state）。返回 `ModelRequest` 可能是 LangChain 期望或忽略。

**验证方法**：
```bash
grep -A 3 "def before_model_call" internagents/date_middleware.py internagents/goal_middleware.py
```

**建议**：
- 与 `RuntimeDateContextMiddleware` / `GoalContextMiddleware` 保持一致的返回习惯
- 如果它们返回 None，改成 None（就地改 `request.state`）
- 如果它们返回 request，保留现状

**影响文件**：`frame_middleware.py:29-66`

**状态**：🟡 待验证后决定

---

### R4 · `agent_local = get_agent_graph(...)` 无 fallback [🔴 需修]

**问题**：`agent_graph.py:~1988`：
```python
agent_local = get_agent_graph("local", "main")   # 无 try/except
```

如果 `_build_agent_graph_for` 失败（比如 model creds 缺失、tool 依赖导入错），**整个 module import 崩溃** → langgraph.json 全部 endpoint 都起不来。

之前老代码有 fallback：`agent_local = _resource_agents.get("local", agent)`。

**建议**：
```python
try:
    agent_local = get_agent_graph("local", "main")
except Exception as e:
    logger.warning(f"get_agent_graph failed for local/main, falling back: {e}")
    agent_local = _resource_agents.get("local", agent)
```

**影响文件**：`agent_graph.py:~1985-1990` 附近

**状态**：🔴 未修，等你批准修

---

### R5 · 公开 tool 名 `create_goal` / `update_goal` / `get_goal` 未 rename [⚠️ 决策]

**问题**：内部逻辑已 Frame-first，但**公开 tool 名字**仍是 `create_goal` / `update_goal` / `get_goal`。这些是 agent 通过 tool_use 调的名字，写在 main.yaml prompt 里。

**如果 rename** 成 `create_frame` / `update_frame` / `get_frame`：
- 要改 `goal_tools.py` 的 `@tool()` decorator
- 要改 `main.yaml` 里所有引用 `create_goal` / `update_goal` / `get_goal` 的提示词
- 要改 `reviewer.yaml` / 其它 YAML 如果有引用
- 要改任何测试
- **半天工作量**

**如果不 rename**：
- 保留 tool 名 = 保留 "goal" 概念在 agent 侧的语言表达
- 内部 Frame 是实现细节，agent 不需要知道
- backward compat 度更高

**建议**：**不 rename**。tool 名代表 agent 的用户界面，Frame 是实现——两者可以名不一样。类似 REST API 保留 `/api/goals` 端点，但内部数据是 Frame。

**状态**：⚠️ 等你决策 rename or 不 rename

---

## Part C · Deferred（当前未做，未来看情况）

### D1 · Memory + rolling compact [📋 明确 defer]

**你之前明确说算了**。这块是 Claude Science 的 §6 章节：
- `memories` 表（user_id + category + content + source_frame_id）
- 上下文超长时压缩到 `compaction_archives` 表
- `RollingCompactMiddleware`

**状态**：📋 v0.2 再看

---

### D2 · Skill 生成/进化模块 [📋 你们独家，未来必做]

Phase 3.5 时只加了 `internagents/skill_evolution.py` 的 Protocol 契约 stub。真正实现（propose/review/commit 三个方法）**未做**。

**状态**：📋 v0.2 实现

---

### D3 · Reviewer 自动派生 [📋 未做]

当前 main frame `status="completed"` 后**不会自动 spawn reviewer**。要触发 reviewer 得手动调 `frame_service.spawn_reviewer(parent_frame)`。

**加自动触发**：给 main graph 加一条 conditional edge："main done → 若配置了 auto_review 则 spawn reviewer child frame → END"。

**状态**：📋 待你决定要不要加

---

### D4 · HTTP MCP OAuth 支持 [❌ 不做]

Claude Science 里的 `*.mcp.claude.com` 托管 MCP + OAuth 握手。你们用 streamable HTTP，OAuth 层跳过。

**状态**：❌ 不做

---

### D5 · Artifact + script-bundle 系统 [📋 未选]

Claude Science §11 的可复现产物系统。你之前 MVP 范围勾选时没选。

**状态**：📋 有需求再说

---

## Part D · 我做过但可能有隐忧、你也许想 review 的角落

### E1 · Docstring 数量 [🟡]

我在多个新文件里写了较详细的 docstring。有些函数 4-5 行的 body 配 10 行 docstring。**你可能觉得冗**。

**位置**：`frame_state.py` / `frame_service.py` / `agent_registry.py`

**建议**：等其他 review 完再决定要不要精简。

---

### E2 · TypedDict 里 NotRequired 用法 [🟡]

`FrameState` 用了 `NotRequired[...]` 表示可选字段（Python 3.11+ 特性）。项目 `pyproject.toml` 里 Python 版本要求是否 ≥3.11？

**验证**：
```bash
grep python_requires OpenClaudeScience/pyproject.toml
```

**状态**：🟡 待验证

---

### E3 · Test 覆盖只测数据结构 [🟡]

6 份 smoke test 共 45 个 case，**都是纯 Python 数据检查**（不接 model）。真正的 `graph.ainvoke(state, config=...)` 端到端从未测过。原因：需要 API key + 网络 + 时间。

**风险**：
- LangGraph state schema 不匹配可能 runtime 才暴露
- middleware 顺序问题
- checkpointer 交互

**建议**：写一个用 FakeListChatModel 的 e2e test（在项目里 `ToolBindableFakeListChatModel` 已存在于 agent_graph.py:49——正是为这个）。

**位置**：新建 `tests/e2e_smoke.py`

**状态**：🟡 建议加，未做

---

## Review 记录

_每次你批准 / 否决 / 提出修改，在此更新_

| 时间 | 项 | 决定 | 备注 |
|---|---|---|---|
| — | — | — | — |

---

## 附录：文件清单

**新增 Python 文件**（可 review 全文）：
- `internagents/frame_state.py`（422 行）
- `internagents/frame_service.py`（684 行）
- `internagents/agent_registry.py`（316 行）
- `internagents/frame_middleware.py`（67 行）

**编辑的 Python 文件**（只 review diff）：
- `internagents/agent_graph.py`（+288 / -21，原 1709 → 现 1976）
- `internagents/goal_tools.py`（+73 / -10，原 111 → 现 174）

**新增数据文件**：
- `internagents/prompts/agents/main.yaml`（146）
- `internagents/prompts/agents/reviewer.yaml`（277）
- `internagents/prompts/agents/bookmarker.yaml`（68）
- `internagents/prompts/agents/onboarding.yaml`（285）

**新增测试**：
- `tests/frame_state_smoke.py`（207）
- `tests/agent_registry_smoke.py`（170）
- `tests/agent_routing_smoke.py`（130）
- `tests/bookmarker_concurrent_smoke.py`（148）
- `tests/frame_integration_smoke.py`（87）
- `tests/routing_wiring_smoke.py`（128）

**新增文档**：
- `docs/webapp.md`
- `docs/frame-model.md`
- `docs/agent-roles.md`
- `docs/agent-routing.md`
- `docs/REVIEW-PLAN.md`（本文件）

**新增脚本**：
- `scripts/web.sh`

**未动的核心文件**（保证 backward compat）：
- `internagents/goal_state.py`（GoalState 定义仍在）
- `internagents/goal_middleware.py`（仍消费 goal 字段）
- `internagents/{date,dynamic_local_backend,kb_sync,mcp_config,mcp_tools,remote_compute_tools,ssh_backend,thread_skill}_middleware.py`（未动）
- 前端 `ui/`（未动）
- Electron `desktop/`（未动）
