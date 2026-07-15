"""Resource configuration for multi-end 天玄·千枢科学发现平台 sessions."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_RESOURCES_FILE = ROOT_DIR / "internagent.resources.json"

BackendKind = Literal["local_shell", "ssh_shell"]


@dataclass(frozen=True)
class ResourceConfig:
    id: str
    label: str
    backend: BackendKind
    workspace: str
    enabled: bool = True
    ssh_command: str | None = None
    kb_path: str | None = None
    kb_command: str = "kb"
    remote_url: str | None = None
    remote_runtime_port: int | None = None
    remote_assistant_id: str | None = None
    remote_backend_release_tag: str | None = None
    remote_backend_fingerprint: str | None = None
    remote_backend_source_repo: str | None = None
    remote_backend_asset_name: str | None = None
    remote_backend_updated_at: str | None = None
    remote_install_mode: str | None = None
    remote_python_path: str | None = None
    remote_conda_command: str | None = None
    timeout: int = 120
    max_output_bytes: int = 100_000

    @property
    def graph_name(self) -> str:
        return f"agent_{self.id}"


def _resource_file() -> Path:
    explicit = os.getenv("INTERNAGENT_RESOURCES_FILE") or _root_env_value(
        "INTERNAGENT_RESOURCES_FILE"
    )
    if not explicit:
        return DEFAULT_RESOURCES_FILE
    path = Path(explicit)
    return path if path.is_absolute() else ROOT_DIR / path


def _root_env_value(name: str) -> str | None:
    env_path = ROOT_DIR / ".env"
    try:
        lines = env_path.read_text().splitlines()
    except OSError:
        return None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if separator != "=" or key.strip() != name:
            continue
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {'"', "'"}
        ):
            return value[1:-1]
        return value
    return None


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _local_runtime_url_from_env() -> str | None:
    port = os.getenv("INTERNAGENTS_LOCAL_RUNTIME_PORT")
    if not port:
        return None
    port = port.strip()
    if not port.isdigit():
        raise ValueError(f"Invalid INTERNAGENTS_LOCAL_RUNTIME_PORT: {port!r}")
    return f"http://127.0.0.1:{int(port)}"


def load_resource_config() -> tuple[str, dict[str, ResourceConfig]]:
    path = _resource_file()
    local_runtime_url = _local_runtime_url_from_env()
    if not path.exists():
        default = ResourceConfig(
            id="local",
            label="Current Machine",
            backend="local_shell",
            workspace=".",
            remote_url=local_runtime_url,
            remote_assistant_id="agent" if local_runtime_url else None,
        )
        return default.id, {default.id: default}

    data = json.loads(path.read_text())
    resources: dict[str, ResourceConfig] = {}
    for item in data.get("resources", []):
        if not _as_bool(item.get("enabled"), True):
            continue
        resource_id = str(item["id"]).strip()
        if not resource_id:
            raise ValueError(f"Invalid empty resource id in {path}")
        if resource_id in resources:
            raise ValueError(f"Duplicate resource id {resource_id!r} in {path}")
        backend = item.get("backend", "local_shell")
        if backend not in {"local_shell", "ssh_shell"}:
            raise ValueError(f"Unsupported backend {backend!r} for resource {resource_id!r}")
        if backend == "ssh_shell" and not str(item.get("ssh_command", "")).strip():
            raise ValueError(f"ssh_shell resource {resource_id!r} requires ssh_command")
        env_remote_url = local_runtime_url if resource_id == "local" else None
        resources[resource_id] = ResourceConfig(
            id=resource_id,
            label=str(item.get("label") or resource_id),
            backend=backend,
            workspace=str(item.get("workspace") or "."),
            ssh_command=(str(item.get("ssh_command")).strip() if item.get("ssh_command") else None),
            kb_path=(str(item.get("kb_path")).strip() if item.get("kb_path") else None),
            kb_command=str(item.get("kb_command") or "kb"),
            remote_url=env_remote_url
            or (str(item.get("remote_url")).strip() if item.get("remote_url") else None),
            remote_runtime_port=(
                int(item.get("remote_runtime_port"))
                if item.get("remote_runtime_port")
                else None
            ),
            remote_assistant_id=(
                str(item.get("remote_assistant_id")).strip()
                if item.get("remote_assistant_id")
                else "agent"
                if env_remote_url
                else None
            ),
            remote_backend_release_tag=(
                str(item.get("remote_backend_release_tag")).strip()
                if item.get("remote_backend_release_tag")
                else None
            ),
            remote_backend_fingerprint=(
                str(item.get("remote_backend_fingerprint")).strip()
                if item.get("remote_backend_fingerprint")
                else None
            ),
            remote_backend_source_repo=(
                str(item.get("remote_backend_source_repo")).strip()
                if item.get("remote_backend_source_repo")
                else None
            ),
            remote_backend_asset_name=(
                str(item.get("remote_backend_asset_name")).strip()
                if item.get("remote_backend_asset_name")
                else None
            ),
            remote_backend_updated_at=(
                str(item.get("remote_backend_updated_at")).strip()
                if item.get("remote_backend_updated_at")
                else None
            ),
            remote_install_mode=(
                str(item.get("remote_install_mode")).strip()
                if item.get("remote_install_mode")
                else None
            ),
            remote_python_path=(
                str(item.get("remote_python_path")).strip()
                if item.get("remote_python_path")
                else None
            ),
            remote_conda_command=(
                str(item.get("remote_conda_command")).strip()
                if item.get("remote_conda_command")
                else None
            ),
            timeout=int(item.get("timeout") or 120),
            max_output_bytes=int(item.get("max_output_bytes") or 100_000),
        )

    if not resources:
        raise ValueError(f"No enabled resources in {path}")

    default_resource = str(data.get("default_resource") or next(iter(resources)))
    if default_resource not in resources:
        raise ValueError(f"default_resource {default_resource!r} is not enabled in {path}")
    return default_resource, resources


def get_resource(resource_id: str | None = None) -> ResourceConfig:
    default_resource, resources = load_resource_config()
    selected = resource_id or default_resource
    try:
        return resources[selected]
    except KeyError as exc:
        raise ValueError(f"Unknown resource {selected!r}") from exc
