# Running InternAgentS as a Webapp

InternAgentS runs natively as a **local webapp** — no desktop application required. The entire system is accessible via your browser at `http://127.0.0.1:3000`.

## Quick Start

To start InternAgentS in web mode:

```bash
bash scripts/web.sh
```

Then open http://127.0.0.1:3000 in your browser.

To auto-open the browser on startup:

```bash
bash scripts/web.sh --open
```

To use a custom port:

```bash
INTERNAGENTS_UI_PORT=3001 bash scripts/web.sh
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Your Browser                            │
│           (http://127.0.0.1:3000)                           │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │         Next.js Web UI                              │   │
│  │    • Chat & composer                                │   │
│  │    • Project file browser                           │   │
│  │    • Settings & skills                              │   │
│  └────────────┬────────────────────────────────────────┘   │
└───────────────┼─────────────────────────────────────────────┘
                │ HTTP requests
       ┌────────┴─────────┐
       │                  │
┌──────▼──────┐    ┌──────▼────────────┐
│ LangGraph   │    │ DeepAgent        │
│ Coordinator │    │ Local Runtime    │
│ (port 2024) │    │ (port 22024)     │
│             │    │                  │
│ • Orchestr. │    │ • Agent state    │
│ • Tool      │    │ • Skills         │
│   registry  │    │ • Workspace      │
│ • Tracing   │    │   filesystem     │
└─────────────┘    └──────────────────┘
```

## Port Reference

| Service | Port | Purpose |
|---------|------|---------|
| **Web UI** | 3000 (default) | Next.js frontend; open this in your browser |
| **Backend** | 2024 | LangGraph coordinator; manages agent lifecycle & tools |
| **Local Runtime** | 22024 | DeepAgent runtime; executes skills & workspace operations |

All ports are bound to `127.0.0.1` (localhost only). External access is not supported in this phase.

## Development Workflow

### Python Backend Changes

The backend runs in LangGraph dev mode with hot-reload enabled. When you modify Python files in `internagents/` or `agent.py`:

1. Changes are detected automatically
2. The backend reloads (~3-5 seconds)
3. Refresh your browser to pick up the changes

### Next.js UI Changes

The UI runs with Vite/Next.js dev mode. When you modify files in `ui/`:

1. Changes are detected and recompiled
2. The browser refreshes automatically (fast refresh)
3. You'll see the changes immediately in the browser

### Environment Variables

Key overrides for web mode:

| Variable | Default | Purpose |
|----------|---------|---------|
| `INTERNAGENTS_UI_PORT` | 3000 | Web server port |
| `INTERNAGENTS_BACKEND_PORT` | 2024 | Backend port |
| `INTERNAGENTS_LOCAL_RUNTIME_PORT` | 22024 | Runtime port |
| `INTERNAGENTS_SKIP_INSTALL` | 0 | Set to 1 to skip npm/pip install |
| `INTERNAGENTS_OPEN_BROWSER` | 0 | Set to 1 to auto-open browser on startup |

## Troubleshooting

**Q: Web UI shows "Service Unavailable" or 502 error**

A: The backend or runtime hasn't started yet. Wait 10-15 seconds for all three services to come online. Check logs:

```bash
tail -f .internagents/logs/backend.log
tail -f .internagents/logs/local-runtime.log
tail -f .internagents/logs/ui.log
```

**Q: Port already in use (e.g., "Address already in use")**

A: Another process is using the port. Either:
- Kill the process: `lsof -i :3000` (then `kill <PID>`)
- Use a different port: `INTERNAGENTS_UI_PORT=3001 bash scripts/web.sh`

**Q: Python package installation fails**

A: Ensure you have Python 3.11+ and npm installed. Then reinstall:

```bash
rm -rf .venv ui/node_modules
bash scripts/web.sh
```

**Q: Changes to Python aren't being picked up**

A: The backend is in hot-reload mode. If changes aren't reflected, check backend.log for syntax errors:

```bash
tail -50 .internagents/logs/backend.log | grep -i error
```

## Electron Desktop (Optional)

The optional Electron desktop wrapper (`desktop/electron/main.cjs`) simply launches these three services and opens the web UI in a native window. It is not required — the webapp runs perfectly in any modern browser.

To use the desktop app instead:

```bash
npm run desktop
```

Both the web and desktop modes use the same backend and runtime services.
