#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# Ensure internal .svc.pjlab.local / .pjlab.local hosts (e.g. MCP servers on
# the internal PJLab cluster) bypass the outbound httpproxy. The system
# no_proxy usually includes .pjlab.org.cn but not the .local variants used
# by K8s service DNS.
_INTERNAL_NO_PROXY=".pjlab.local,.svc.pjlab.local"
export no_proxy="${no_proxy:+$no_proxy,}$_INTERNAL_NO_PROXY"
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}$_INTERNAL_NO_PROXY"

HOST="127.0.0.1"
BACKEND_PORT="${INTERNAGENTS_BACKEND_PORT:-2024}"
DEFAULT_LOCAL_RUNTIME_PORT="${INTERNAGENTS_LOCAL_RUNTIME_PORT_START:-22024}"
LOCAL_RUNTIME_PORT="${INTERNAGENTS_LOCAL_RUNTIME_PORT:-$DEFAULT_LOCAL_RUNTIME_PORT}"
LOCAL_RUNTIME_PORT_EXPLICIT=0
if [ -n "${INTERNAGENTS_LOCAL_RUNTIME_PORT:-}" ]; then
  LOCAL_RUNTIME_PORT_EXPLICIT=1
fi
LOCAL_RUNTIME_PORT_SCAN_COUNT="${INTERNAGENTS_LOCAL_RUNTIME_PORT_SCAN_COUNT:-32}"
UI_PORT="${INTERNAGENTS_UI_PORT:-3000}"
UI_TURBO="${INTERNAGENTS_UI_TURBO:-1}"
UI_WARMUP="${INTERNAGENTS_UI_WARMUP:-1}"
UI_WARMUP_TIMEOUT="${INTERNAGENTS_UI_WARMUP_TIMEOUT:-60}"
OPEN_BROWSER="${INTERNAGENTS_OPEN_BROWSER:-1}"
SKIP_INSTALL="${INTERNAGENTS_SKIP_INSTALL:-0}"
ASSISTANT_ID="${INTERNAGENTS_ASSISTANT_ID:-agent_local}"
LANGGRAPH_NO_RELOAD="${INTERNAGENTS_LANGGRAPH_NO_RELOAD:-1}"
LANGGRAPH_JOBS_PER_WORKER="${INTERNAGENTS_LANGGRAPH_JOBS_PER_WORKER:-5}"

RUNTIME_DIR="$ROOT_DIR/.internagents"
LOG_DIR="$RUNTIME_DIR/logs"
PID_DIR="$RUNTIME_DIR/pids"
LANGGRAPH_STATE_DIR="$RUNTIME_DIR/langgraph-state"
BACKEND_STATE_DIR="$LANGGRAPH_STATE_DIR/backend"
LOCAL_RUNTIME_STATE_DIR="$LANGGRAPH_STATE_DIR/local-runtime"
BACKEND_LOG="$LOG_DIR/backend.log"
LOCAL_RUNTIME_LOG="$LOG_DIR/local-runtime.log"
UI_LOG="$LOG_DIR/ui.log"
BACKEND_PID_FILE="$PID_DIR/backend.pid"
LOCAL_RUNTIME_PID_FILE="$PID_DIR/local-runtime.pid"
UI_PID_FILE="$PID_DIR/ui.pid"

PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
BACKEND_URL="http://$HOST:$BACKEND_PORT"
LOCAL_RUNTIME_URL="http://$HOST:$LOCAL_RUNTIME_PORT"
UI_URL="http://$HOST:$UI_PORT"
APP_URL="$UI_URL/?assistantId=$ASSISTANT_ID"

BACKEND_OWNED=0
LOCAL_RUNTIME_OWNED=0
UI_OWNED=0
BACKEND_PID=""
LOCAL_RUNTIME_PID=""
UI_PID=""

log() {
  printf '[天玄·千枢科学发现平台] %s\n' "$*"
}

langgraph_reload_args() {
  if [ "$LANGGRAPH_NO_RELOAD" = "1" ]; then
    printf '%s\n' "--no-reload"
  fi
}

langgraph_jobs_args() {
  if [ -n "$LANGGRAPH_JOBS_PER_WORKER" ] && [ "$LANGGRAPH_JOBS_PER_WORKER" != "0" ]; then
    printf '%s\n' "--n-jobs-per-worker"
    printf '%s\n' "$LANGGRAPH_JOBS_PER_WORKER"
  fi
}

die() {
  printf '[天玄·千枢科学发现平台] Error: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

is_uint() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

set_local_runtime_port() {
  LOCAL_RUNTIME_PORT="$1"
  LOCAL_RUNTIME_URL="http://$HOST:$LOCAL_RUNTIME_PORT"
}

ensure_langgraph_state_dir() {
  local state_dir="$1"
  mkdir -p "$state_dir"

  if [ "${OS:-}" != "Windows_NT" ] && ln -sfn "$ROOT_DIR/agent.py" "$state_dir/agent.py" 2>/dev/null; then
    return 0
  fi

  rm -f "$state_dir/agent.py"
  {
    printf '%s\n' "import importlib.util"
    printf '%s\n' "import os"
    printf '%s\n' "import sys"
    printf '%s\n' "from pathlib import Path"
    printf '%s\n' ""
    printf '%s\n' "_root = Path(os.environ[\"INTERNAGENTS_GRAPH_ROOT\"])"
    printf '%s\n' "_spec = importlib.util.spec_from_file_location(\"_internagents_real_agent\", _root / \"agent.py\")"
    printf '%s\n' "if _spec is None or _spec.loader is None:"
    printf '%s\n' "    raise RuntimeError(\"Unable to load 天玄·千枢科学发现平台 graph entrypoint.\")"
    printf '%s\n' "_module = importlib.util.module_from_spec(_spec)"
    printf '%s\n' "sys.modules[_spec.name] = _module"
    printf '%s\n' "_spec.loader.exec_module(_module)"
    printf '%s\n' "globals().update({name: getattr(_module, name) for name in dir(_module) if not name.startswith(\"__\")})"
  } > "$state_dir/agent.py"
}

print_log_tail() {
  local label="$1"
  local file="$2"
  if [ -f "$file" ]; then
    printf '\n---- %s log: %s ----\n' "$label" "$file" >&2
    tail -80 "$file" >&2 || true
    printf '%s\n\n' '---- end log ----' >&2
  fi
}

url_ok() {
  curl -fsS --max-time 2 "$1" >/dev/null 2>&1
}

port_open() {
  "$PYTHON_BIN" - "$HOST" "$1" <<'PY' >/dev/null 2>&1
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.5)
    sys.exit(0 if sock.connect_ex((host, port)) == 0 else 1)
PY
}

select_local_runtime_port() {
  is_uint "$LOCAL_RUNTIME_PORT" || die "Invalid local runtime port: $LOCAL_RUNTIME_PORT"
  is_uint "$LOCAL_RUNTIME_PORT_SCAN_COUNT" || die "Invalid local runtime port scan count: $LOCAL_RUNTIME_PORT_SCAN_COUNT"

  if [ "$LOCAL_RUNTIME_PORT_EXPLICIT" = "1" ]; then
    set_local_runtime_port "$LOCAL_RUNTIME_PORT"
    return 0
  fi

  local start="$LOCAL_RUNTIME_PORT"
  local count="$LOCAL_RUNTIME_PORT_SCAN_COUNT"
  local end=$((start + count - 1))
  local port
  for ((port = start; port <= end; port++)); do
    local url="http://$HOST:$port"
    if url_ok "$url/ok"; then
      set_local_runtime_port "$port"
      if [ "$port" != "$start" ]; then
        log "Selected existing healthy local runtime: $LOCAL_RUNTIME_URL"
      fi
      return 0
    fi
    if ! port_open "$port"; then
      set_local_runtime_port "$port"
      if [ "$port" != "$start" ]; then
        log "Selected free local runtime port: $LOCAL_RUNTIME_PORT"
      fi
      return 0
    fi
    log "Skipping occupied local runtime port: $port"
  done

  die "No available local runtime port in range $start-$end."
}

wait_for_url() {
  local label="$1"
  local url="$2"
  local log_file="$3"
  local pid="${4:-}"
  local timeout="${5:-120}"
  local start
  start="$(date +%s)"

  while true; do
    if url_ok "$url"; then
      log "$label is ready: $url"
      return 0
    fi

    if [ -n "$pid" ] && ! kill -0 "$pid" >/dev/null 2>&1; then
      print_log_tail "$label" "$log_file"
      die "$label process exited before becoming ready."
    fi

    if [ $(( $(date +%s) - start )) -ge "$timeout" ]; then
      print_log_tail "$label" "$log_file"
      die "$label did not become ready within ${timeout}s: $url"
    fi

    sleep 1
  done
}

warm_frontend_routes() {
  if [ "$UI_WARMUP" != "1" ]; then
    return 0
  fi

  local routes=(/ /about /config /connect /knowledge /projects /showcase /skills)
  log "Warming ${#routes[@]} routes to avoid on-demand compile..."

  local pids=()
  local tmpdir
  tmpdir="$(mktemp -d)"
  local i=0
  local route
  for route in "${routes[@]}"; do
    (
      local start end
      start="$(date +%s)"
      if curl -sS -o /dev/null --max-time "$UI_WARMUP_TIMEOUT" "$UI_URL$route" 2>/dev/null; then
        end="$(date +%s)"
        printf '  ✓ %s (%ss)\n' "$route" "$((end - start))" > "$tmpdir/$i"
      else
        end="$(date +%s)"
        printf '  ✗ %s (%ss, will lazy-compile on first click)\n' "$route" "$((end - start))" > "$tmpdir/$i"
      fi
    ) &
    pids+=($!)
    i=$((i + 1))
  done

  local wait_pid
  for wait_pid in "${pids[@]}"; do
    wait "$wait_pid" 2>/dev/null || true
  done

  local f
  for f in "$tmpdir"/*; do
    [ -f "$f" ] || continue
    printf '%s' "$(cat "$f")"
  done
  rm -rf "$tmpdir"

  log "Routes warmed."
}

read_alive_pid_file() {
  local pid_file="$1"
  [ -f "$pid_file" ] || return 1

  local pid
  pid="$(tr -cd '0-9' < "$pid_file" 2>/dev/null || true)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" >/dev/null 2>&1 || return 1
  printf '%s\n' "$pid"
}

adopt_restarted_process() {
  local url="$1"
  local pid_file="$2"
  local timeout="${3:-20}"
  local start
  start="$(date +%s)"

  while true; do
    local replacement_pid
    replacement_pid="$(read_alive_pid_file "$pid_file" || true)"
    if [ -n "$replacement_pid" ] && url_ok "$url/ok"; then
      printf '%s\n' "$replacement_pid"
      return 0
    fi

    if [ $(( $(date +%s) - start )) -ge "$timeout" ]; then
      return 1
    fi

    sleep 1
  done
}

terminate_pid() {
  local label="$1"
  local pid="$2"
  [ -n "$pid" ] || return 0
  kill -0 "$pid" >/dev/null 2>&1 || return 0

  log "Stopping $label (pid $pid)..."
  if command -v pkill >/dev/null 2>&1; then
    pkill -TERM -P "$pid" >/dev/null 2>&1 || true
  fi
  kill -TERM "$pid" >/dev/null 2>&1 || true

  for _ in 1 2 3 4 5; do
    kill -0 "$pid" >/dev/null 2>&1 || return 0
    sleep 1
  done

  if command -v pkill >/dev/null 2>&1; then
    pkill -KILL -P "$pid" >/dev/null 2>&1 || true
  fi
  kill -KILL "$pid" >/dev/null 2>&1 || true
}

cleanup() {
  local exit_code="${1:-$?}"
  trap - EXIT INT TERM

  if [ "$UI_OWNED" = "1" ]; then
    terminate_pid "frontend" "$UI_PID"
    rm -f "$UI_PID_FILE"
  fi
  if [ "$BACKEND_OWNED" = "1" ]; then
    terminate_pid "backend" "$BACKEND_PID"
    rm -f "$BACKEND_PID_FILE"
  fi
  if [ "$LOCAL_RUNTIME_OWNED" = "1" ]; then
    terminate_pid "local runtime" "$LOCAL_RUNTIME_PID"
    rm -f "$LOCAL_RUNTIME_PID_FILE"
  fi

  exit "$exit_code"
}

trap 'cleanup 130' INT
trap 'cleanup 143' TERM
trap 'cleanup $?' EXIT

open_browser() {
  [ "$OPEN_BROWSER" != "0" ] || return 0

  if command -v open >/dev/null 2>&1; then
    open "$APP_URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$APP_URL" >/dev/null 2>&1 || true
  else
    log "Open this URL in your browser: $APP_URL"
  fi
}

install_dependencies() {
  require_command python3

  if [ ! -x "$PYTHON_BIN" ]; then
    log "Creating Python virtual environment at .venv..."
    python3 -m venv "$ROOT_DIR/.venv"
  fi

  if [ ! -f "$ROOT_DIR/.env" ] && [ -f "$ROOT_DIR/.env.example" ]; then
    log "Creating .env from .env.example..."
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  fi

  if "$PYTHON_BIN" -c "import internagents.agent_graph" >/dev/null 2>&1; then
    log "Python package already installed; skipping 'pip install -e .'."
  else
    log "Installing Python package in editable mode..."
    "$PYTHON_BIN" -m pip install -e .
  fi

  if [ ! -d "$ROOT_DIR/ui/node_modules" ]; then
    require_command npm
    log "Installing UI dependencies..."
    (cd "$ROOT_DIR/ui" && npm install --legacy-peer-deps --ignore-scripts)
  fi
}

prepare_environment() {
  mkdir -p "$LOG_DIR" "$PID_DIR"
  ensure_langgraph_state_dir "$BACKEND_STATE_DIR"
  ensure_langgraph_state_dir "$LOCAL_RUNTIME_STATE_DIR"

  if [ "$SKIP_INSTALL" = "1" ]; then
    [ -x "$PYTHON_BIN" ] || die ".venv is missing. Unset INTERNAGENTS_SKIP_INSTALL or create it first."
    [ -d "$ROOT_DIR/ui/node_modules" ] || die "ui/node_modules is missing. Unset INTERNAGENTS_SKIP_INSTALL or run npm install first."
  else
    install_dependencies
  fi

  require_command curl
  require_command npm
  select_local_runtime_port
}

start_local_runtime() {
  if url_ok "$LOCAL_RUNTIME_URL/ok"; then
    log "Reusing existing local runtime: $LOCAL_RUNTIME_URL"
    rm -f "$LOCAL_RUNTIME_PID_FILE"
    return 0
  fi

  if port_open "$LOCAL_RUNTIME_PORT"; then
    die "Port $LOCAL_RUNTIME_PORT is in use, but $LOCAL_RUNTIME_URL/ok is not healthy."
  fi

  : > "$LOCAL_RUNTIME_LOG"
  log "Starting local agent runtime on $LOCAL_RUNTIME_URL..."
  (
    cd "$LOCAL_RUNTIME_STATE_DIR"
    INTERNAGENTS_GRAPH_ROOT="$ROOT_DIR" \
    INTERNAGENT_PROCESS_ROLE=runtime \
    INTERNAGENT_RUNTIME_ID=local \
    INTERNAGENTS_LOCAL_RUNTIME_PORT="$LOCAL_RUNTIME_PORT" \
      "$PYTHON_BIN" -m langgraph_cli dev \
        --host "$HOST" \
        --port "$LOCAL_RUNTIME_PORT" \
        --no-browser \
        $(langgraph_reload_args) \
        $(langgraph_jobs_args) \
        --config "$ROOT_DIR/langgraph.runtime.json"
  ) >>"$LOCAL_RUNTIME_LOG" 2>&1 &

  LOCAL_RUNTIME_PID="$!"
  LOCAL_RUNTIME_OWNED=1
  printf '%s\n' "$LOCAL_RUNTIME_PID" > "$LOCAL_RUNTIME_PID_FILE"
  wait_for_url "Local runtime" "$LOCAL_RUNTIME_URL/ok" "$LOCAL_RUNTIME_LOG" "$LOCAL_RUNTIME_PID"
}

start_backend() {
  if url_ok "$BACKEND_URL/ok"; then
    log "Reusing existing backend: $BACKEND_URL"
    rm -f "$BACKEND_PID_FILE"
    return 0
  fi

  if port_open "$BACKEND_PORT"; then
    die "Port $BACKEND_PORT is in use, but $BACKEND_URL/ok is not healthy."
  fi

  : > "$BACKEND_LOG"
  log "Starting backend on $BACKEND_URL..."
  (
    cd "$BACKEND_STATE_DIR"
    INTERNAGENTS_GRAPH_ROOT="$ROOT_DIR" \
    INTERNAGENTS_LOCAL_RUNTIME_PORT="$LOCAL_RUNTIME_PORT" \
    "$PYTHON_BIN" -m langgraph_cli dev \
      --host "$HOST" \
      --port "$BACKEND_PORT" \
      --no-browser \
      $(langgraph_reload_args) \
      $(langgraph_jobs_args) \
      --config "$ROOT_DIR/langgraph.json"
  ) >>"$BACKEND_LOG" 2>&1 &

  BACKEND_PID="$!"
  BACKEND_OWNED=1
  printf '%s\n' "$BACKEND_PID" > "$BACKEND_PID_FILE"
  wait_for_url "Backend" "$BACKEND_URL/ok" "$BACKEND_LOG" "$BACKEND_PID"
}

start_frontend() {
  if url_ok "$UI_URL"; then
    log "Reusing existing frontend: $UI_URL"
    rm -f "$UI_PID_FILE"
    return 0
  fi

  if port_open "$UI_PORT"; then
    die "Port $UI_PORT is in use, but $UI_URL is not serving 天玄·千枢科学发现平台."
  fi

  : > "$UI_LOG"
  local ui_script="dev"
  if [ "$UI_TURBO" = "1" ]; then
    ui_script="dev:turbo"
    log "Starting frontend on $UI_URL (Turbopack)..."
  else
    log "Starting frontend on $UI_URL (webpack)..."
  fi
  (
    cd "$ROOT_DIR/ui"
    INTERNAGENTS_APP_ROOT="$ROOT_DIR" \
    INTERNAGENTS_LOCAL_RUNTIME_PORT="$LOCAL_RUNTIME_PORT" \
    NEXT_PUBLIC_LANGGRAPH_DEPLOYMENT_URL="$BACKEND_URL" \
    NEXT_PUBLIC_LANGGRAPH_ASSISTANT_ID="$ASSISTANT_ID" \
      npm run "$ui_script" -- --hostname "$HOST" --port "$UI_PORT"
  ) >>"$UI_LOG" 2>&1 &

  UI_PID="$!"
  UI_OWNED=1
  printf '%s\n' "$UI_PID" > "$UI_PID_FILE"
  wait_for_url "Frontend" "$UI_URL" "$UI_LOG" "$UI_PID"
  warm_frontend_routes
}

monitor_processes() {
  log "天玄·千枢科学发现平台 is running."
  log "UI:      $APP_URL"
  log "Backend: $BACKEND_URL"
  log "Runtime: $LOCAL_RUNTIME_URL"
  log "Logs:    $LOG_DIR"
  log "Press Ctrl+C to stop services started by this script."

  while true; do
    if [ "$LOCAL_RUNTIME_OWNED" = "1" ] && ! kill -0 "$LOCAL_RUNTIME_PID" >/dev/null 2>&1; then
      replacement_pid="$(adopt_restarted_process "$LOCAL_RUNTIME_URL" "$LOCAL_RUNTIME_PID_FILE" || true)"
      if [ -n "$replacement_pid" ]; then
        LOCAL_RUNTIME_PID="$replacement_pid"
        log "Local runtime was restarted by the UI; now monitoring pid $LOCAL_RUNTIME_PID."
        continue
      fi
      print_log_tail "local runtime" "$LOCAL_RUNTIME_LOG"
      die "Local runtime process exited."
    fi
    if [ "$BACKEND_OWNED" = "1" ] && ! kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
      replacement_pid="$(adopt_restarted_process "$BACKEND_URL" "$BACKEND_PID_FILE" || true)"
      if [ -n "$replacement_pid" ]; then
        BACKEND_PID="$replacement_pid"
        log "Backend was restarted by the UI; now monitoring pid $BACKEND_PID."
        continue
      fi
      print_log_tail "backend" "$BACKEND_LOG"
      die "Backend process exited."
    fi
    if [ "$UI_OWNED" = "1" ] && ! kill -0 "$UI_PID" >/dev/null 2>&1; then
      print_log_tail "frontend" "$UI_LOG"
      die "Frontend process exited."
    fi
    sleep 2
  done
}

prepare_environment
start_local_runtime
start_backend
start_frontend
open_browser
monitor_processes
