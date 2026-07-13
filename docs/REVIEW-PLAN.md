# InternAgentS `feature/frame-agents` Review Plan

> **活的 review 追踪文档**。每 review 一项，在 status 里改标记。等所有 R1-R5 处理完，本文档可以删掉再 merge。

**分支**：`feature/frame-agents`（从 `main` 分出 8 commits）
**当前 head**：`<pending frame-migration commit>`
**测试**：49/49 全绿
**净增量**：新增 frame_tools.py；删除 goal_state.py / goal_middleware.py / goal_tools.py / test_goal_state.py

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

### C8 · `<pending>` — Frame 完全替代 Goal · 删除 goal_* 模块 [🟡]

**核心决定**：不做派生视图。Goal 概念从代码库彻底移除，Frame 是唯一的执行单元表达。

**新增文件**：
- `internagents/frame_tools.py`（约 155 行）— 新的 3 个 tool：`get_frame`、`create_frame`、`update_frame`（tool 名 R5 决定：**改名**，与新概念对齐）

**改写文件**：
- `internagents/frame_state.py`：删掉 `frame_from_goal` / `goal_from_frame` 桥接函数；新增 `TERMINAL_FRAME_STATUSES`、`ACTIVE_FRAME_STATUSES`、`MAX_FRAME_OBJECTIVE_CHARS`、`validate_frame_objective`、`validate_token_budget`、`frame_response`（GoalState 五大等效原语的 Frame 版本）；`create_root_frame` 加 `token_budget` 参数（存到 evolution_context）
- `internagents/frame_middleware.py`：从"只填 state 字段"升级到"能替代 GoalContextMiddleware 全部功能"。新增 `FRAME_COMMAND_INSTRUCTIONS`、`frame_system_prompt`、`render_frame_context`（把 objective + budget 注入 system message）、`FrameContextMiddleware`（带 `before_agent` 和 `wrap_model_call`）、`_active_frame`、`_recover_frame_from_messages`
- `internagents/agent_graph.py`：
  - imports 全部换成 `frame_*`
  - `GOAL_CONTINUATION_TURNS_KEY = "goalContinuationTurns"` → `FRAME_CONTINUATION_TURNS_KEY = "frameContinuationTurns"`
  - `GOAL_MAX_AUTO_TURNS_ENV` → `FRAME_MAX_AUTO_TURNS_ENV`
  - `InternAgentState`：删除 `goal` 字段；`goalContinuationTurns` → `frameContinuationTurns`；加 `input_data`/`output_data`
  - `_goal_status/_goal_continuation_turns/_with_goal_continuation_accounting/_should_continue_goal/_goal_blocked_after_remote_runtime_error` 全部改名 `_frame_*`；逻辑改为读 `state["frame_status"]`
  - `GoalContextMiddleware()` → `FrameContextMiddleware()`（两处）
  - `goal_system_prompt` → `frame_system_prompt`
  - `goal_tools()` → `frame_tools()`
- `internagents/agent_registry.py`：`middlewares` tuple 里 `"goal"` → `"frame"`

**删除文件**：
- `internagents/goal_state.py` （148 行删除）
- `internagents/goal_middleware.py` （161 行删除）
- `internagents/goal_tools.py` （174 行删除）
- `tests/test_goal_state.py` （101 行删除）

**改写测试**：
- `tests/frame_state_smoke.py`：删除 `test_goal_bridge_*` 两个测试；新增 `test_validate_frame_objective` / `test_validate_token_budget` / `test_root_frame_with_token_budget` / `test_frame_response_shape` / `test_status_sets_disjoke`（共 16 case）
- `tests/frame_integration_smoke.py`：删除 `test_goal_from_frame_shape`、`test_goal_tools_imports_frame_state`；新增 `test_frame_tools_registered`、`test_agent_registry_uses_frame_middleware`、`test_agent_graph_no_goal_imports`（6 case）

**净变化**：+476 / -824（-348 行代码）

**同时解决**：
- R1 ✅ 字段名统一 `frame_id` / `frame_status`（`_command_with_frame` 与 `InternAgentState` 一致）
- R5 ✅ Tool 名决策：**改名**（`create_goal`→`create_frame` 等）

**Review 重点**：
- `FrameContextMiddleware` 是否真能替代 `GoalContextMiddleware`（render_frame_context 内容对不对，system message 拼接位置）
- `_command_with_frame` 写入 state 的字段与 `InternAgentState` 定义完全一致
- `frame_middleware.py` 与 `frame_tools.py` 各自的 `_frame_from_state` 逻辑一致（当前是两处，可能应该抽公用）

**状态**：🟡 等 review

---

### C9 · `<pending>` — Frame auto-lifecycle (β) · 自动创建 + objective 自动捕获 + 工具从 3 减 2 [🟡]

**核心改造**：从"用户手动 `create_frame` 触发"改成"系统自动创建执行单元"，对齐 Claude Science β 方案。

**新增**：
- `FrameRootMiddleware` 在 `internagents/frame_middleware.py` — 线程启动或上一 frame 变 terminal 时自动创建新 root frame，objective 从最近的 HumanMessage 自动抽取
- `_extract_objective_from_messages(messages: list[AnyMessage]) -> str` 辅助函数 — 遍历消息历史找最后一条 HumanMessage，支持多模态内容块

**删除**：
- `create_frame` 工具 — 不再支持工具创建 frame（自动化替代）
- `FRAME_COMMAND_INSTRUCTIONS` 常量 — 静态注入冗余（动态注入已足够）
- `frame_system_prompt()` 函数 — 删掉 Goal 时代遗留的"进入 frame mode 说明"

**编辑**：
- `internagents/frame_middleware.py`：
  - 修改 `_active_frame()` — 去掉 objective 必需检查（objective 永远存在）
  - 修改 `render_frame_context()` — 重写措辞，去掉"Continue working within the active frame"和"This frame persists"这类同义反复，改成清晰的"Task context: <objective>...进展跨轮持续...验证真实目标"
- `internagents/frame_tools.py`：`frame_tools()` 返回 2 个工具而不是 3 个 `[get_frame, update_frame]`
- `internagents/agent_graph.py`：
  - import 删 `frame_system_prompt`，加 `FrameRootMiddleware`
  - `_agent_system_prompt` 改为返回 `base_prompt`（不再拼静态指令）
  - 两处 middleware 装配点（`_filter_middlewares_for_agent` 和 runtime agent 构建）都加 `FrameRootMiddleware()` 作为第一个 middleware
- `tests/frame_integration_smoke.py`：删 `frame_system_prompt` import，`test_frame_tools_registered` 期待值改 3→2
- **新增** `tests/frame_lifecycle_smoke.py` — 8 个 case 验证目标提取 + root middleware 创建逻辑 + 自动转换场景

**β 方案数据流**：
```
用户发消息 → LangGraph invoke
    ↓
FrameRootMiddleware.before_agent():
    if (state.frame_id 无) or (frame.status ∈ TERMINAL_FRAME_STATUSES):
        objective = _extract_objective_from_messages(state.messages)  # 找最近 HumanMessage
        create_root_frame(..., input_data={"objective": objective})
        → 返回新 frame 的字段 (frame_id, root_frame_id, ..., input_data)
    ↓
FrameContextMiddleware.wrap_model_call():
    frame = _active_frame(state)       # 无 objective guard，总能拿到
    system_message += render_frame_context(frame)
    ↓
LLM 从第一轮就有 objective 上下文
    ↓
LLM 调 update_frame("completed") 或 ("blocked")
    ↓ state.frame_status = "completed" / "blocked"
用户后续发新消息
    ↓ FrameRootMiddleware 检测终止状态 → 开新 frame
```

**解决的 issue**：
- **无 Ensure ↔ create_frame 冲突** — 原 C5 时 `FrameEnsureMiddleware` 阻止 `create_frame` 调用的设计矛盾，β 方案根本消除（frame 自动，无手动创建入口）
- **工具精简** — 从 3→2，专注于"查询"和"标记完成/阻止"

**保留不变**：
- `FrameState` 数据模型 / `get_frame` / `update_frame` 语义
- 子 frame 派生（`spawn_reviewer` 等）
- 路由 / continuation 逻辑
- 其他 middleware（date / skill / kb_sync）

**净变化**：+95 行新代码 / -40 行删除（create_frame 工具 + 常量 + 函数）

**Review 重点**：
- `_extract_objective_from_messages` 对多模态内容和 dict-shaped 消息的处理
- `FrameRootMiddleware._needs_new_frame()` 对 TERMINAL_FRAME_STATUSES 的判定
- `render_frame_context` 新措辞是否清晰、与旧版"继续"语义对齐
- `_active_frame` 是否真的不再需要 objective guard

**测试结果**：
- 旧 6 个 smoke 文件：49→48 case（删 1 个 test_frame_tools_registered 期待值），48/48 绿
- 新 lifecycle smoke：8 case 全绿

**状态**：🟡 等 review

---

## Part B · 待修的 5 个已识别问题


### R1 · `_command_with_frame` 写入的字段名 vs `InternAgentState` 定义 [✅ 已修 (C8)]

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

**修复方案**：C8 里把 `InternAgentState` 的 `id`/`status` 都统一为 `frame_id`/`frame_status`；`frame_tools.py:_command_with_frame` 写入的字段与 `InternAgentState` 完全一致。

**状态**：✅ 已修（C8）

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

### R5 · 公开 tool 名 `create_goal` / `update_goal` / `get_goal` [✅ 已改名 (C8)]

**决定**：全部改名 → `create_frame` / `update_frame` / `get_frame`。理由：既然 Goal 概念从代码库完全消失，工具名保留 goal 会造成 agent 侧和 code 侧的语义脱节。

**状态**：✅ 已改名（C8）

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
- `internagents/frame_state.py`（≈390 行 —— 已扩展，含 GoalState 全部等效原语，删除桥接）
- `internagents/frame_service.py`（684 行）
- `internagents/agent_registry.py`（316 行）
- `internagents/frame_middleware.py`（≈270 行 —— 已从 67 行扩展到含 FrameContextMiddleware）
- `internagents/frame_tools.py`（≈155 行 —— 新增，替代 goal_tools.py）

**编辑的 Python 文件**（只 review diff）：
- `internagents/agent_graph.py`（+97 / -97 in C8；累计 +286 / -117）
- `internagents/agent_registry.py`（1 行 middleware tuple 改名）

**已删除的 Python 文件**：
- ~~`internagents/goal_state.py`~~（148 行删除，C8）
- ~~`internagents/goal_middleware.py`~~（161 行删除，C8）
- ~~`internagents/goal_tools.py`~~（174 行删除，C8）
- ~~`tests/test_goal_state.py`~~（101 行删除，C8）

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

**未动的核心文件**（未涉及）：
- `internagents/{date,dynamic_local_backend,kb_sync,mcp_config,mcp_tools,remote_compute_tools,ssh_backend,thread_skill}_middleware.py`（未动）
- 前端 `ui/`（未动）
- Electron `desktop/`（未动）
