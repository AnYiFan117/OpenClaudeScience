#!/usr/bin/env bash
# tail-frame.sh — watch live Frame lifecycle events from the InternAgentS logs.
#
# Usage:
#   bash scripts/tail-frame.sh              # tail local-runtime.log
#   bash scripts/tail-frame.sh backend      # tail backend.log
#   bash scripts/tail-frame.sh all          # tail both logs
#
# Strips ANSI color escape codes and filters to lines that contain frame
# lifecycle markers:
#   🖼️  [Frame] Root · CREATE
#   🖼️  [Frame] Root · SKIP
#   🖼️  [Frame] Ctx  · INJECT
#   🖼️  [Frame] Tool · UPDATE

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$ROOT_DIR/.internagents/logs"

TARGET="${1:-local-runtime}"

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
    printf '[tail-frame] 📜 watching both logs (interleaved)\n'
    tail -n 200 -f "$LOG_DIR/local-runtime.log" "$LOG_DIR/backend.log" 2>&1 | strip_ansi | filter_frame
    ;;
  *)
    printf '[tail-frame] Usage: %s [local-runtime|backend|all]\n' "$0" >&2
    exit 1
    ;;
esac
