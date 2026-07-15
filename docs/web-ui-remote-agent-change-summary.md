# Web UI RemoteAgent 同步改动说明

对比基线：`origin/main` (`8a9fe0c`)

## 目标

- 让 Web UI 的 LangGraph 调用方式尽量对齐 Textual UI 的 RemoteAgent。
- 让 Web UI 能拿到与 Textual UI 更一致的 stream 信息，包括消息、状态更新、完整 values、subgraph namespace 和 interrupt。
- 调整本地 agent 配置，取消 `execute` 审批，并将系统提示改为中文科研助手定位。

## 主要改动

- 新增 `WebRemoteAgent` 封装，统一管理 LangGraph SDK `Client`、assistant 解析、thread 查询、state 更新和 stream 参数。
- 默认启用 `messages-tuple`、`updates`、`values` 三类 stream mode，并开启 `streamSubgraphs`，贴近 Textual UI 的事件可见性。
- 新增 stream event layer，捕获 SDK 原始 stream 事件，补齐 `useStream` 没有直接暴露的 interrupt/subgraph 信息。
- Web UI 的 assistant、thread、chat 提交逻辑改为通过 RemoteAgent 访问，减少页面和 hooks 内部直接拼 SDK 调用的分散逻辑。
- 修复 orphan interrupt 渲染：当后端 thread 已进入 interrupted 状态但没有可见工具调用行时，仍展示审批组件。
- `deepagent.config.json` 移除 `execute` 审批配置，并更新 system prompt 为中文科研助手和 天玄·千枢科学发现平台 身份说明。

## 不包含

- `generate_pdf.py`
- `multimodal_agents_report.md`

以上两个文件是临时文件，未纳入本次改动说明和提交范围。

## 验证

- `python3 -m json.tool deepagent.config.json`
- `python3 -m json.tool ui/deepagent-ui.config.json`
- `git diff --check`
- `npm run lint`：通过，保留现有 Fast Refresh warning。
- `npm run build`：通过。
