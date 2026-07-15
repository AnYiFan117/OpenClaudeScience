<div align="center">
  <p>
    <img src="./docs/assets/readme/internagents-banner.png" alt="天玄·千枢科学发现平台 banner" width="100%">
  </p>

  <h1 align="center">天玄·千枢科学发现平台: Bringing the Claude Science experience to the open-source world.</h1>
  <p align="center">
    Built on DeepAgents and LangGraph to extend research agent runtimes across project context, files, skills, remote resources, and human approvals.
  </p>
  <p align="center">
    <a href="https://github.com/qzzqzzb/OpenClaudeScience/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/qzzqzzb/OpenClaudeScience?style=social"></a>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white">
    <img alt="Next.js" src="https://img.shields.io/badge/Next.js-16-black?logo=nextdotjs">
    <img alt="LangGraph" src="https://img.shields.io/badge/LangGraph-runtime-1f6feb">
    <img alt="DeepAgents" src="https://img.shields.io/badge/DeepAgents-agent%20runtime-4f46e5">
    <img alt="License" src="https://img.shields.io/badge/License-MIT-green">
    <img alt="Status" src="https://img.shields.io/badge/status-active%20development-0f766e">
  </p>
  <p align="center">
    <strong>English</strong> | <a href="./README_CN.md">简体中文</a>
  </p>
  <p>
    <a href="#highlights">Highlights</a>
    · <a href="#example-workflows">Workflows</a>
    · <a href="#feature-overview">Feature Overview</a>
    · <a href="#quick-start">Quick Start</a>
    · <a href="#security-and-privacy">Security</a>
    · <a href="#architecture">Architecture</a>
    · <a href="#development">Development</a>
    · <a href="#license">License</a>
  </p>
</div>

天玄·千枢科学发现平台 is a local-first research workspace built on DeepAgents/LangGraph,
with project runtimes, skills, remote resources, and approval flows designed for
scientific work.

## Highlights

- **DeepAgents/LangGraph, extended for research**: 天玄·千枢科学发现平台 adapts the
  runtime, workspace, skills, tools, and approval flow around scientific
  projects.
- **Remote environments without the ceremony**: connect SSH workspaces, sync
  remote runtimes, inspect logs, and approve remote compute jobs from the
  conversation.
- **Science skills out of the box**: literature search, result analysis,
  figures, paper writing, documents, slides, and domain workflows are reusable
  skills.
- **MCP/SCP, plus your choice of model**: connect external tools through
  MCP/SCP, and use cloud models, private gateways, or local model servers.
- **Local-first data control**: project files, secrets, and runtime state stay
  on machines you control by default, without requiring Claude, Claude Science,
  or any fixed cloud service.
- **Built for real scientific files**: browse, search, preview, and reference
  PDFs, Office files, images, molecular structures, scientific data outputs, and
  generated artifacts.

## Web Mode (No Desktop App Needed)

天玄·千枢科学发现平台 runs as a local webapp accessible from any browser. To start without Electron:

```bash
bash scripts/web.sh
```

Then open http://127.0.0.1:3000 in your browser. See [docs/webapp.md](docs/webapp.md) for details, environment overrides, and troubleshooting.

## Example Workflows

- Paper and report triage: attach papers or Markdown reports, ask the agent to
  summarize claims, extract assumptions, compare methods, and leave generated
  notes in the project.
- Scientific artifact inspection: browse project files, preview PDFs, Office
  files, images, molecular structures, and scientific data outputs, then ask the
  agent to explain what changed.
- Experiment and code iteration: ask the agent to inspect code, run local
  commands, create result files, and summarize outputs with links back to the
  files in the workspace.
- Skill-guided sessions: enable reusable skills for literature search, result
  analysis, figures, documents, slides, or domain-specific research workflows.
- Remote compute handoff: register a Linux SSH host, review the proposed compute
  job in chat, approve it, and let the local backend harvest configured outputs.

### Analyzing Potential Lattice Distortions in PbTiO3 Perovskites Under A-Site/B-Site Substitution

![PbTiO3 A-site/B-site substitution lattice distortion analysis](./docs/assets/readme/pbtio3-ab-site-substitution.gif)

### Caffeine Computational Chemistry Study

![Caffeine computational chemistry study](./docs/assets/readme/caffeine-computational-chemistry.gif)

### Construct simplified 2D/3D models of a Y-shaped microfluidic mixer

![Construct simplified 2D/3D models of a Y-shaped microfluidic mixer](./docs/assets/readme/y-shaped-microfluidic-mixer.gif)

## Feature Overview

天玄·千枢科学发现平台 brings chat, project sessions, file browsing, and local runtime
status into a single research workbench. The right panel keeps project files and
artifacts visible while the center conversation stays focused on the current
task.

![天玄·千枢科学发现平台 workspace preview](./docs/assets/readme/workspace-preview-en.jpeg)

### Local-First Research Workspace

天玄·千枢科学发现平台 is organized as a three-panel workspace:

| Area | What it does |
| --- | --- |
| Left sidebar | project navigation, sessions, settings, and skill entry points |
| Center | chat, composer, attachments, mentions, and agent progress |
| Right panel | project files, previews, provenance, runtime info, and connector context |

Project files are accessed through the workspace API rather than direct UI file
system calls. The file panel supports directory navigation, grid/list views,
search, and previews for common research artifacts.

### Skills and Science Capability Library

Skills are reusable capabilities that can be enabled for an agent or session.
天玄·千枢科学发现平台 searches shared user catalogs first, then project catalogs:

```text
~/.internagents/myskills
~/.internagents/imported-skills
skills
.internagents/imported-skills
```

The settings UI supports built-in skills, imported skills, and science skills.
Imported skills are copied into a user-level catalog so the same capability can
be reused across multiple projects.

### Model, Authorization, and Appearance Settings

The unified settings page manages:

- model provider, Base URL, API key, and model ID
- project directory
- Linux SSH compute host registration and job activity
- tool-call authorization mode
- language and appearance
- archived conversations
- skills and connector configuration

The UI includes both Chinese and English copy.

### MCP and SCP Connectors

天玄·千枢科学发现平台 can load external tools through MCP server configuration and can
prepare SCP Hub access for science skill workflows.

Local MCP config locations:

```text
~/.deepagents/.mcp.json
<repo>/.deepagents/.mcp.json
<repo>/.mcp.json
INTERNAGENT_MCP_CONFIG_FILE
```

Connector secrets, private commands, headers, and endpoints should stay local.

### Linux SSH Compute Jobs

天玄·千枢科学发现平台 has an experimental Linux-only SSH compute provider. This is
separate from SSH remote runtime setup: the local backend keeps the current
conversation session and submits detached jobs to a registered Linux SSH host.

Current scope:

- Linux hosts only.
- SSH hosts are registered by `Host` alias from the local `~/.ssh/config`.
  Address, user, port, `ProxyJump`, and key settings come from OpenSSH.
- Jobs run as detached `bash` processes under a per-job scratch directory.
- Job status is polled over SSH; outputs matching configured globs are harvested
  back as base64 payloads when they fit under the configured size cap.
- Settings > Compute registers and probes SSH hosts. Job submission happens from
  the conversation when the agent proposes a remote compute tool call.
- Proposed remote compute calls appear as permission cards in chat. The user
  must approve the card before the local backend submits the SSH job.

Local compute state lives under `.internagents/compute/`, which is ignored by
git. The local API surface is:

```text
GET  /api/compute/ssh-hosts
POST /api/compute/ssh-hosts
GET  /api/compute/remote-jobs
POST /api/compute/remote-jobs
GET  /api/compute/remote-jobs/:jobId
```

API calls require the local token stored at `.internagents/compute/api-token`:

```bash
TOKEN="$(cat .internagents/compute/api-token)"
curl -X POST http://127.0.0.1:3000/api/compute/ssh-hosts \
  -H 'Content-Type: application/json' \
  -H "X-InternAgentS-Compute-Token: $TOKEN" \
  -d '{"host":"my-linux-host","notes":"Use sbatch on gpu partition; conda envs live under ~/envs."}'
```

## Quick Start

### Requirements

- Python 3.11+
- Node.js and npm. The UI uses `ui/package-lock.json` as the canonical lockfile.
- An OpenAI-compatible model endpoint, or the option to configure one later

### Start the Workbench

```bash
cp .env.example .env
./scripts/dev.sh
```

The launcher prepares the local environment and starts three services:

On first run, it creates `.venv`, installs the Python package in editable mode,
and runs `npm install --legacy-peer-deps --ignore-scripts` in `ui/`. Use
`INTERNAGENTS_SKIP_INSTALL=1` only after these dependencies are already present.

| Service | Default URL | Purpose |
| --- | --- | --- |
| UI | `http://127.0.0.1:3000` | Next.js workbench |
| Coordinator | `http://127.0.0.1:2024` | LangGraph API for the workbench frontend |
| Local runtime | `http://127.0.0.1:22024` | Project-scoped DeepAgent runtime |

Open:

```text
http://127.0.0.1:3000/?assistantId=agent_local
```

Logs are written to:

```text
.internagents/logs/backend.log
.internagents/logs/local-runtime.log
.internagents/logs/ui.log
```

Press `Ctrl+C` in the launcher terminal to stop the services started by the
script.

### Configure a Model

You can configure a model during first setup, or skip it and return later from
Settings. For an OpenAI-compatible endpoint:

```env
INTERNAGENTS_MODEL_PROVIDER=openai_compatible
OPENAI_BASE_URL=https://api.example.com/v1
OPENAI_API_KEY=sk-...
DEEPAGENT_MODEL=your-model-id
```

DeepSeek's official OpenAI-compatible endpoint can also be configured with
provider-specific aliases:

```env
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

When the OpenAI-compatible provider is selected, `DEEPSEEK_API_KEY`,
`DEEPSEEK_BASE_URL`, and `DEEPSEEK_MODEL` are treated as aliases for the
corresponding OpenAI-compatible API key, base URL, and model.

Keep API keys and machine-specific paths in local `.env` or runtime config
files. Do not commit secrets.

### Useful Overrides

```bash
INTERNAGENTS_UI_PORT=3001 ./scripts/dev.sh
INTERNAGENTS_BACKEND_PORT=2025 ./scripts/dev.sh
INTERNAGENTS_OPEN_BROWSER=0 ./scripts/dev.sh
INTERNAGENTS_SKIP_INSTALL=1 ./scripts/dev.sh
```

## Security and Privacy

天玄·千枢科学发现平台 is local-first by default. Project files are accessed through the
workspace API, and runtime state is kept under local directories such as
`.internagents/`.

- Keep model API keys, MCP headers, SCP Hub keys, server addresses, SSH aliases,
  and machine-specific paths in local `.env` or runtime config files.
- Do not commit `.env`, `internagent.resources.local.json`, private SSH
  material, logs, pids, uploads, LangGraph state, or active skill runtime
  directories.
- Tool-call authorization modes can require approval before file writes or other
  actions. SSH compute jobs always appear as approval cards before submission.
- When connecting to a remote Agent service, review that service endpoint first:
  the remote service owns its own workspace, tools, and resource policy.
- Connector configuration should keep secrets local. Shared examples should be
  sanitized and should prefer placeholder endpoints and keys.

## Architecture

```mermaid
flowchart LR
  Browser["Browser UI<br/>Next.js"] --> Coordinator["LangGraph coordinator<br/>agent.py"]
  Coordinator --> Runtime["Local runtime<br/>DeepAgent"]
  Runtime --> Workspace["Project workspace<br/>files, shell, skills"]
  Runtime --> MCP["MCP servers"]
  Runtime --> SCP["SCP Hub"]
```

## Repository Map

```text
agent.py                         LangGraph entrypoint shim
deepagent.config.json            local backend, skills, model, and UI defaults
internagents/                    graph assembly, backend adapters, middleware, tools, and resource loading
scripts/dev.sh                   one-command local development launcher
ui/                              Next.js workbench UI
skills/                          bundled project skills
docs/                            user guides and design notes
```

## Development

Run these checks before opening a pull request:

```bash
git diff --check
python3 -m json.tool deepagent.config.json >/dev/null
python3 -m json.tool internagent.resources.json >/dev/null
python3 -m json.tool ui/deepagent-ui.config.json >/dev/null
npm --prefix ui run lint
(cd ui && npx tsc --noEmit)
npm --prefix ui run build
```

For Python backend changes:

```bash
.venv/bin/python -m compileall agent.py internagents
.venv/bin/python -c "import agent; print(agent.MODEL)"
```

## Contributing

天玄·千枢科学发现平台 is shaped as an open research tool. Helpful contributions include:

- bug reports with clear reproduction steps
- UI polish that keeps existing workflows stable
- new skills with examples and safe defaults
- connector integrations that keep secrets local
- documentation for installation, configuration, and research workflows

Please keep changes scoped. DeepAgents is treated as an external SDK, so
天玄·千枢科学发现平台 should extend it through public APIs, adapters, middleware, tools,
and local resource configuration rather than patching SDK internals.

## License

天玄·千枢科学发现平台 is released under the [MIT License](LICENSE).

## Roadmap Notes

Near-term areas of work:

- clearer skill marketplace and installation flow
- stronger MCP and SCP configuration UX
- richer previews for scientific artifacts
- better remote resource management
- packaged desktop workflows

## Contributors

<a href="https://github.com/qzzqzzb/OpenClaudeScience/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=qzzqzzb/OpenClaudeScience" alt="Contributors" />
</a>
