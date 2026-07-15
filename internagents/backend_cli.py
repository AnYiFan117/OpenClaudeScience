"""Command line entrypoints for packaged 天玄·千枢科学发现平台 backend runtimes."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Sequence


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _write_runtime_config(
    state_dir: Path,
    *,
    resource_id: str,
    label: str,
    workspace: str,
    remote_url: str,
) -> Path:
    config_path = state_dir / "internagent.runtime.local.json"
    config = {
        "default_resource": resource_id,
        "resources": [
            {
                "id": resource_id,
                "label": label or resource_id,
                "backend": "local_shell",
                "workspace": workspace,
                "remote_url": remote_url,
                "remote_assistant_id": "agent",
                "enabled": True,
            }
        ],
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    return config_path


def _state_dir(args: argparse.Namespace, install_dir: Path) -> Path:
    value = getattr(args, "state_dir", None)
    if value:
        return Path(value).expanduser().resolve()
    return install_dir


def _runtime_pid_file(state_dir: Path, resource_id: str) -> Path:
    return state_dir / ".internagents" / "pids" / f"runtime-{resource_id}.pid"


def _state_env(state_dir: Path) -> dict[str, str]:
    env_path = state_dir / ".env"
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if separator != "=":
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {'"', "'"}
        ):
            value = value[1:-1]
        values[key] = value
    return values


def _tail_text(path: Path, *, lines: int = 80, max_chars: int = 8000) -> str:
    if not path.exists():
        return ""
    try:
        content = path.read_text(errors="replace")
    except OSError:
        return ""
    tail = "\n".join(content.splitlines()[-lines:])
    if len(tail) > max_chars:
        return tail[-max_chars:]
    return tail


def _wait_for_health(
    url: str,
    timeout_seconds: int,
    *,
    log_file: Path | None = None,
) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/ok", timeout=1) as response:
                if 200 <= response.status < 300:
                    print("runtime ok")
                    return
        except Exception:
            time.sleep(1)
    message = f"runtime health check timed out: {url}/ok"
    if log_file is not None:
        tail = _tail_text(log_file)
        if tail:
            message = f"{message}\n\nRuntime log tail ({log_file}):\n{tail}"
    raise RuntimeError(message)


def _start_runtime(args: argparse.Namespace) -> int:
    install_dir = Path(args.install_dir).expanduser().resolve()
    state_dir = _state_dir(args, install_dir)
    workspace = Path(args.workspace).expanduser()
    install_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    remote_url = f"http://{args.host}:{args.port}"
    config_path = _write_runtime_config(
        state_dir,
        resource_id=args.resource_id,
        label=args.label,
        workspace=str(workspace.resolve(strict=False)),
        remote_url=remote_url,
    )

    runtime_dir = state_dir / ".internagents"
    log_dir = runtime_dir / "logs"
    pid_dir = runtime_dir / "pids"
    log_dir.mkdir(parents=True, exist_ok=True)
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = _runtime_pid_file(state_dir, args.resource_id)
    log_file = log_dir / f"runtime-{args.resource_id}.log"

    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
        except ValueError:
            pid = 0
        if pid > 0 and _is_running(pid):
            print(f"runtime already running pid={pid}")
            _wait_for_health(remote_url, args.health_timeout, log_file=log_file)
            return 0

    state_config = state_dir / "deepagent.config.json"
    env = {
        **_state_env(state_dir),
        **os.environ,
        "INTERNAGENT_PROCESS_ROLE": "runtime",
        "INTERNAGENT_RUNTIME_ID": args.resource_id,
        "INTERNAGENT_ENV_FILE": str(state_dir / ".env"),
        "INTERNAGENT_RESOURCES_FILE": str(config_path),
    }
    if state_config.exists():
        env["DEEPAGENT_CONFIG"] = str(state_config)
    command = [
        sys.executable,
        "-m",
        "langgraph_cli",
        "dev",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--no-browser",
        "--no-reload",
        "--config",
        "langgraph.runtime.json",
    ]
    with log_file.open("ab") as log:
        process = subprocess.Popen(
            command,
            cwd=install_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    pid_file.write_text(f"{process.pid}\n")
    print(f"runtime started pid={process.pid}")
    _wait_for_health(remote_url, args.health_timeout, log_file=log_file)
    return 0


def _stop_runtime(args: argparse.Namespace) -> int:
    install_dir = Path(args.install_dir).expanduser().resolve()
    state_dir = _state_dir(args, install_dir)
    pid_file = _runtime_pid_file(state_dir, args.resource_id)
    if not pid_file.exists():
        print("runtime pid file not found")
        return 0
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        pid_file.unlink(missing_ok=True)
        print("runtime pid file was invalid")
        return 0
    if pid > 1 and _is_running(pid):
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            os.kill(pid, signal.SIGTERM)
        print(f"runtime stopped pid={pid}")
    pid_file.unlink(missing_ok=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="internagents-backend")
    subcommands = parser.add_subparsers(dest="command", required=True)

    runtime = subcommands.add_parser("runtime")
    runtime_subcommands = runtime.add_subparsers(dest="runtime_command", required=True)

    start = runtime_subcommands.add_parser("start")
    start.add_argument("--install-dir", required=True)
    start.add_argument("--state-dir")
    start.add_argument("--resource-id", required=True)
    start.add_argument("--label", required=True)
    start.add_argument("--workspace", required=True)
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=22024)
    start.add_argument("--health-timeout", type=int, default=60)
    start.set_defaults(func=_start_runtime)

    stop = runtime_subcommands.add_parser("stop")
    stop.add_argument("--install-dir", required=True)
    stop.add_argument("--state-dir")
    stop.add_argument("--resource-id", required=True)
    stop.set_defaults(func=_stop_runtime)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
