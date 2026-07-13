#!/usr/bin/env bash
set -euo pipefail

# InternAgentS Web Mode
#
# This script runs InternAgentS as a local webapp (browser-accessible).
# No Electron desktop app needed — just a browser.
#
# Usage:
#   ./scripts/web.sh                 # Start in headless mode (you open the browser)
#   ./scripts/web.sh --open          # Start and auto-open browser
#   INTERNAGENTS_UI_PORT=3001 ./scripts/web.sh  # Use a custom port

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Parse arguments
AUTO_OPEN=0
for arg in "$@"; do
  case "$arg" in
    --open)
      AUTO_OPEN=1
      ;;
    *)
      printf 'Usage: %s [--open]\n' "$(basename "$0")" >&2
      exit 1
      ;;
  esac
done

UI_PORT="${INTERNAGENTS_UI_PORT:-3000}"
HOST="127.0.0.1"
APP_URL="http://$HOST:$UI_PORT/?assistantId=agent_local"

log() {
  printf '[InternAgentS Web Mode] %s\n' "$*"
}

log "=========================================="
log "Starting InternAgentS as a local webapp"
log "=========================================="
log ""
log "This will start three services:"
log "  • LangGraph Backend Coordinator (port 2024)"
log "  • DeepAgent Local Runtime (port 22024)"
log "  • Next.js Web UI (port $UI_PORT)"
log ""
log "Once running, open your browser to:"
log "  $APP_URL"
log ""
log "Press Ctrl+C to stop all services."
log "=========================================="
log ""

# Run dev.sh, but with optional browser opening
if [ "$AUTO_OPEN" = "1" ]; then
  INTERNAGENTS_OPEN_BROWSER=1 "$ROOT_DIR/scripts/dev.sh"
else
  INTERNAGENTS_OPEN_BROWSER=0 "$ROOT_DIR/scripts/dev.sh"
fi
