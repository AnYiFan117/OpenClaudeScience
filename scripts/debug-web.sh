#!/usr/bin/env bash
# debug-web.sh — one-click clean + restart the InternAgentS web stack in debug mode.
#
# What it does:
#   1. kills every process listening on ports 2024 / 22024 / 3000
#      (delegates to kill-all.sh)
#   2. removes stale pid files
#   3. optionally truncates .internagents/logs/*.log (--fresh-logs)
#   4. detects existing .venv + ui/node_modules and skips reinstall
#      (sets INTERNAGENTS_SKIP_INSTALL=1)
#   5. exports INTERNAGENT_FRAME_DEBUG=1 so 🖼️  [Frame] lines land in the log
#   6. execs scripts/dev.sh
#
# Usage:
#   bash scripts/debug-web.sh                # clean + restart, keep old logs
#   bash scripts/debug-web.sh --fresh-logs   # also truncate log files first
#   bash scripts/debug-web.sh --open         # ...and auto-open browser
#
# In a second terminal:
#   bash scripts/tail-frame.sh               # follow 🖼️  [Frame] events

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$ROOT_DIR/.internagents/logs"

FRESH_LOGS=0
AUTO_OPEN=0
for arg in "$@"; do
  case "$arg" in
    --fresh-logs) FRESH_LOGS=1 ;;
    --open)       AUTO_OPEN=1 ;;
    -h|--help)
      sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$arg" >&2
      printf 'Usage: %s [--fresh-logs] [--open]\n' "$(basename "$0")" >&2
      exit 1
      ;;
  esac
done

banner() {
  printf '\n[debug-web] ─────────────────────────────────────\n'
  printf '[debug-web] %s\n' "$*"
  printf '[debug-web] ─────────────────────────────────────\n'
}

# ─── 1) Kill any lingering servers ──────────────────────────────────────
banner "Step 1/4 · killing existing servers"
bash "$SCRIPT_DIR/kill-all.sh" || true

# ─── 2) Optionally truncate logs ─────────────────────────────────────────
if [ "$FRESH_LOGS" = "1" ]; then
  banner "Step 2/4 · truncating old log files"
  : > "$LOG_DIR/backend.log"       2>/dev/null || true
  : > "$LOG_DIR/local-runtime.log" 2>/dev/null || true
  : > "$LOG_DIR/ui.log"            2>/dev/null || true
  printf '[debug-web] logs cleared: %s\n' "$LOG_DIR"
else
  banner "Step 2/4 · keeping existing logs (pass --fresh-logs to wipe)"
fi

# ─── 3) Skip reinstall if venv + node_modules already good ──────────────
banner "Step 3/4 · dependency check"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
skip_install=0
if [ -x "$PYTHON_BIN" ] \
   && "$PYTHON_BIN" -c "import internagents.agent_graph" 2>/dev/null \
   && [ -d "$ROOT_DIR/ui/node_modules" ]; then
  printf '[debug-web] ✅ .venv importable + ui/node_modules present → INTERNAGENTS_SKIP_INSTALL=1\n'
  export INTERNAGENTS_SKIP_INSTALL=1
  skip_install=1
else
  printf '[debug-web] ⚠️  missing deps detected — will install first time\n'
  [ -x "$PYTHON_BIN" ] || printf '[debug-web]   - .venv/bin/python missing\n'
  [ -x "$PYTHON_BIN" ] && ! "$PYTHON_BIN" -c "import internagents.agent_graph" 2>/dev/null \
    && printf '[debug-web]   - package not importable (pip install needed)\n'
  [ -d "$ROOT_DIR/ui/node_modules" ] || printf '[debug-web]   - ui/node_modules missing (npm install needed)\n'
fi

# ─── 4) Start web with Frame debug ──────────────────────────────────────
banner "Step 4/4 · launching web stack"
export INTERNAGENT_FRAME_DEBUG=1
if [ "$AUTO_OPEN" = "1" ]; then
  export INTERNAGENTS_OPEN_BROWSER=1
else
  export INTERNAGENTS_OPEN_BROWSER=0
fi

printf '[debug-web] 🚀 launching dev.sh with INTERNAGENT_FRAME_DEBUG=1\n'
printf '[debug-web] 💡 in another terminal: bash %s/tail-frame.sh\n' "$SCRIPT_DIR"
printf '[debug-web] 💡 UI:  http://127.0.0.1:%s/?assistantId=agent_local\n' "${INTERNAGENTS_UI_PORT:-3000}"
if [ "$skip_install" = "0" ]; then
  printf '[debug-web] ⚠️  first-time install may take a minute…\n'
fi
printf '\n'

exec "$SCRIPT_DIR/dev.sh"
