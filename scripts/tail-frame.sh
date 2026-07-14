#!/usr/bin/env bash
# tail-frame.sh — watch live Frame lifecycle events from the InternAgentS logs.
#
# Usage:
#   bash scripts/tail-frame.sh                # tail BOTH logs (default)
#   bash scripts/tail-frame.sh backend        # only backend.log
#   bash scripts/tail-frame.sh local-runtime  # only local-runtime.log
#
# Strips ANSI color escape codes and filters to lines containing 🖼️  [Frame]
# lifecycle markers:
#   Root · CREATE   — FrameRootMiddleware creates a fresh frame
#   Ctx  · INJECT   — FrameContextMiddleware injects objective into system msg
#   Tool · UPDATE   — update_frame tool transitions status
#
# NOTE: middleware prints usually land in backend.log (that's where the
# agent graph actually runs). local-runtime.log is the runtime port and
# typically only shows HTTP traffic. Defaulting to `all` is safest.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$ROOT_DIR/.internagents/logs"

TARGET="${1:-all}"

strip_ansi() {
  sed 's/\x1b\[[0-9;]*m//g'
}

filter_frame() {
  grep --line-buffered -E "🖼️ \s*\[Frame\]"
}

tail_log() {
  local log_file="$1"
  if [ ! -f "$log_file" ]; then
    printf '[tail-frame] ❌ log not found: %s\n' "$log_file" >&2
    return 1
  fi
  printf '[tail-frame] 📜 watching %s\n' "$log_file"
  tail -n 200 -f "$log_file" 2>&1 | strip_ansi | filter_frame
}

case "$TARGET" in
  local-runtime | runtime)
    tail_log "$LOG_DIR/local-runtime.log"
    ;;
  backend)
    tail_log "$LOG_DIR/backend.log"
    ;;
  all | both)
    printf '[tail-frame] 📜 watching backend.log + local-runtime.log (interleaved)\n'
    tail -n 200 -f "$LOG_DIR/backend.log" "$LOG_DIR/local-runtime.log" 2>&1 | strip_ansi | filter_frame
    ;;
  *)
    printf '[tail-frame] Usage: %s [all|backend|local-runtime]\n' "$0" >&2
    exit 1
    ;;
esac
