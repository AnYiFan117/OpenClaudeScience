#!/usr/bin/env bash
# kill-all.sh — force-kill every 天玄·千枢科学发现平台 dev process on ports 2024/22024/3000.
#
# Use when `scripts/dev.sh` cleanup didn't get everything — for example after
# closing the terminal without Ctrl+C, or when the UI has spawned its own
# langgraph runtime that outlived dev.sh.

set -uo pipefail

PORTS=(2024 22024 3000)

# Extract listener PIDs for a given port from `ss -tlnp` output.
# Uses grep+cut instead of awk regex so `:PORT ` boundaries are literal.
pids_on_port() {
  local port="$1"
  ss -tlnp 2>/dev/null \
    | grep -E "[:.]${port} " \
    | grep -oE 'pid=[0-9]+' \
    | cut -d= -f2 \
    | sort -u
}

echo "[kill-all] scanning ports ${PORTS[*]}"

killed_any=0
for port in "${PORTS[@]}"; do
  pids="$(pids_on_port "$port")"
  if [ -z "$pids" ]; then
    echo "[kill-all] port $port: nothing listening"
    continue
  fi
  for pid in $pids; do
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
      continue
    fi
    cmd="$(ps -o cmd= -p "$pid" 2>/dev/null | head -c 100)"
    echo "[kill-all] port $port pid=$pid cmd=$cmd"
    kill -TERM "$pid" 2>/dev/null || true
    killed_any=1

    # For Next.js: kill the `node next dev` parent too, else it respawns the child immediately
    ppid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
    if [ -n "$ppid" ] && [ "$ppid" != "1" ] && kill -0 "$ppid" 2>/dev/null; then
      pcmd="$(ps -o cmd= -p "$ppid" 2>/dev/null | head -c 200)"
      if printf '%s' "$pcmd" | grep -qE "next(-server)?( dev)?|npm.*dev"; then
        echo "[kill-all] port $port parent pid=$ppid cmd=$pcmd (respawn source)"
        kill -TERM "$ppid" 2>/dev/null || true
      fi
    fi
  done
done

if [ "$killed_any" = "1" ]; then
  echo "[kill-all] sent SIGTERM; waiting 3s..."
  sleep 3

  # SIGKILL anything still holding a port
  for port in "${PORTS[@]}"; do
    pids="$(pids_on_port "$port")"
    for pid in $pids; do
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "[kill-all] SIGKILL survivor pid=$pid on port $port"
        kill -KILL "$pid" 2>/dev/null || true
      fi
    done
  done
fi

# Clean up stale pid files
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
rm -f "$ROOT_DIR/.internagents/pids/"*.pid 2>/dev/null || true

echo "[kill-all] ✅ done. Verify:"
ss -tlnp 2>/dev/null | grep -E ':(2024|22024|3000) ' || echo "  (all three ports free)"
