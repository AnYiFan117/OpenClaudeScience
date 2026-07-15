"""Dynamic local backend — thin passthrough to LocalShellBackend.

Real paths in, real paths out. No path translation, no `skill://` URI, no
HOST_ROOTS access control. The model sees the deployment's actual filesystem
(and skills at their real host locations like
`~/.internagents/myskills/<name>/`), which is the same trust model as
Cursor / Claude Desktop running against the user's own machine.

Value added over a bare `LocalShellBackend`:
  1. Dynamic workspace resolution — the workspace root is looked up from
     the live resource config each call, so switching workspaces in the UI
     takes effect without restarting the graph.
  2. PYTHONPATH injection for `active_skills_path` — lets the python/bash
     tools `import` skill modules by short name without the model
     constructing absolute paths every time.
  3. Per-run workspace override via runtime metadata — for future
     multi-workspace LangGraph runs.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import SandboxBackendProtocol

from internagents.internagent_resources import ResourceConfig, load_resource_config


class DynamicLocalShellBackend(SandboxBackendProtocol):
    """Passthrough backend that resolves its workspace root from live resource config."""

    def __init__(
        self,
        *,
        resource_id: str,
        fallback_root: Path,
        inherit_env: bool = True,
        timeout: int = 120,
        max_output_bytes: int = 100_000,
        workspace_override: str | None = None,
        active_skills_path: Path | None = None,
    ) -> None:
        self.resource_id = resource_id
        self.fallback_root = fallback_root
        self.inherit_env = inherit_env
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        self.workspace_override = workspace_override
        self.active_skills_path = active_skills_path
        self._sandbox_id = f"dynamic-local-{resource_id}-{uuid.uuid4().hex[:8]}"

    @property
    def id(self) -> str:
        return self._sandbox_id

    def _resource(self) -> ResourceConfig | None:
        try:
            _, resources = load_resource_config()
        except Exception:
            return None
        return resources.get(self.resource_id)

    def _resolve_workspace_value(self, workspace: str) -> Path | None:
        path = Path(workspace).expanduser()
        if path == Path("."):
            return self.fallback_root
        if not path.is_absolute():
            path = self.fallback_root / path
        try:
            resolved = path.resolve()
            if resolved.is_dir():
                return resolved
        except OSError:
            return None
        return None

    def _resolve_workspace(self, resource: ResourceConfig | None) -> Path:
        if self.workspace_override:
            override = self._resolve_workspace_value(self.workspace_override)
            if override is not None:
                return override
        if resource is None:
            return self.fallback_root
        configured = self._resolve_workspace_value(resource.workspace)
        return configured or self.fallback_root

    def _active_skill_dirs(self) -> list[Path]:
        """Every currently-activated skill directory (SKILL.md present)."""
        if self.active_skills_path is None or not self.active_skills_path.is_dir():
            return []
        dirs: list[Path] = []
        try:
            entries = sorted(self.active_skills_path.iterdir())
        except OSError:
            return []
        for entry in entries:
            try:
                resolved = entry.resolve()
            except OSError:
                continue
            if resolved.is_dir() and (resolved / "SKILL.md").is_file():
                dirs.append(resolved)
        return dirs

    def _shell_env_overrides(self) -> dict[str, str]:
        """Prepend active skill dirs to PYTHONPATH so `import kernel`
        (etc.) works from python/bash tools without the model constructing
        absolute paths or manipulating sys.path itself."""
        skill_dirs = self._active_skill_dirs()
        if not skill_dirs:
            return {}
        parts = [str(d) for d in skill_dirs]
        parent_pp = os.environ.get("PYTHONPATH", "")
        if parent_pp:
            parts.append(parent_pp)
        return {"PYTHONPATH": os.pathsep.join(parts)}

    def _backend(self) -> LocalShellBackend:
        resource = self._resource()
        return LocalShellBackend(
            root_dir=self._resolve_workspace(resource),
            inherit_env=self.inherit_env,
            virtual_mode=False,
            env=self._shell_env_overrides() or None,
            timeout=resource.timeout if resource is not None else self.timeout,
            max_output_bytes=(
                resource.max_output_bytes
                if resource is not None
                else self.max_output_bytes
            ),
        )

    def ls(self, path: str):
        return self._backend().ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000):
        return self._backend().read(file_path, offset, limit)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None):
        return self._backend().grep(pattern, path, glob)

    def glob(self, pattern: str, path: str = "/"):
        return self._backend().glob(pattern, path)

    def write(self, file_path: str, content: str):
        return self._backend().write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ):
        return self._backend().edit(file_path, old_string, new_string, replace_all)

    def upload_files(self, files: list[tuple[str, bytes]]):
        return self._backend().upload_files(files)

    def download_files(self, paths: list[str]):
        return self._backend().download_files(paths)

    def execute(self, command: str, *, timeout: int | None = None):
        return self._backend().execute(command, timeout=timeout)


def workspace_override_from_runtime(runtime: Any) -> str | None:
    """Extract the workspace path attached to this run, if present."""

    config = getattr(runtime, "config", None)
    context = getattr(runtime, "context", None)
    candidates: list[Any] = []

    if isinstance(config, dict):
        metadata = config.get("metadata")
        configurable = config.get("configurable")
        if isinstance(metadata, dict):
            candidates.append(metadata.get("internagents_workspace_path"))
        if isinstance(configurable, dict):
            candidates.append(configurable.get("internagents_workspace_path"))

    if isinstance(context, dict):
        candidates.append(context.get("internagents_workspace_path"))

    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class DynamicLocalShellBackendFactory:
    """Create a per-run backend using workspace metadata when available."""

    def __init__(
        self,
        *,
        resource_id: str,
        fallback_root: Path,
        inherit_env: bool = True,
        timeout: int = 120,
        max_output_bytes: int = 100_000,
        active_skills_path: Path | None = None,
    ) -> None:
        self.resource_id = resource_id
        self.fallback_root = fallback_root
        self.inherit_env = inherit_env
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        self.active_skills_path = active_skills_path

    def __call__(self, runtime: Any) -> DynamicLocalShellBackend:
        return DynamicLocalShellBackend(
            resource_id=self.resource_id,
            fallback_root=self.fallback_root,
            inherit_env=self.inherit_env,
            timeout=self.timeout,
            max_output_bytes=self.max_output_bytes,
            workspace_override=workspace_override_from_runtime(runtime),
            active_skills_path=self.active_skills_path,
        )
