"""SSH-backed DeepAgents backend for fixed remote workspaces."""

from __future__ import annotations

import base64
import json
import posixpath
import shlex
import subprocess
from pathlib import PurePosixPath
from typing import Any

from deepagents.backends.protocol import (
    SandboxBackendProtocol,
    EditResult,
    ExecuteResponse,
    FileData,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)


class SshShellBackend(SandboxBackendProtocol):
    """Run DeepAgents filesystem and shell operations in a fixed SSH workspace."""

    def __init__(
        self,
        *,
        ssh_command: str,
        workspace: str,
        timeout: int = 120,
        max_output_bytes: int = 100_000,
    ) -> None:
        if not ssh_command.strip():
            raise ValueError("ssh_command is required")
        if not workspace.strip():
            raise ValueError("workspace is required")
        self.ssh_command = ssh_command
        self.workspace = workspace
        self._default_timeout = timeout
        self._max_output_bytes = max_output_bytes

    @property
    def id(self) -> str:
        return f"ssh:{self.ssh_command}:{self.workspace}"

    def _remote_path(self, path: str) -> str:
        raw = path or "/"
        if raw.startswith("~"):
            raise ValueError("Path traversal not allowed")
        rel = raw.lstrip("/")
        parts = PurePosixPath(rel).parts if rel else ()
        if any(part in {"..", ""} for part in parts):
            raise ValueError("Path traversal not allowed")
        if not rel or rel == ".":
            return self.workspace
        return posixpath.join(self.workspace.rstrip("/"), rel)

    def _run_remote(self, script: str, *, timeout: int | None = None) -> ExecuteResponse:
        effective_timeout = timeout if timeout is not None else self._default_timeout
        argv = [*shlex.split(self.ssh_command), "bash", "-lc", script]
        try:
            result = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output=f"Error: SSH command timed out after {effective_timeout} seconds.",
                exit_code=124,
                truncated=False,
            )
        except Exception as exc:  # noqa: BLE001
            return ExecuteResponse(
                output=f"Error executing SSH command ({type(exc).__name__}): {exc}",
                exit_code=1,
                truncated=False,
            )

        output_parts: list[str] = []
        if result.stdout:
            output_parts.append(result.stdout)
        if result.stderr:
            output_parts.extend(f"[stderr] {line}" for line in result.stderr.strip().split("\n"))
        output = "\n".join(output_parts) if output_parts else "<no output>"
        truncated = False
        if len(output) > self._max_output_bytes:
            output = output[: self._max_output_bytes]
            output += f"\n\n... Output truncated at {self._max_output_bytes} bytes."
            truncated = True
        if result.returncode != 0:
            output = f"{output.rstrip()}\n\nExit code: {result.returncode}"
        return ExecuteResponse(output=output, exit_code=result.returncode, truncated=truncated)

    def _python_json(self, source: str, payload: dict[str, Any], *, timeout: int | None = None) -> tuple[dict[str, Any] | None, str | None]:
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        script = "python3 - <<'PY'\n" + source + "\nPY\n" + shlex.quote(encoded)
        script = "PAYLOAD=" + shlex.quote(encoded) + " python3 - <<'PY'\n" + source + "\nPY"
        result = self._run_remote(script, timeout=timeout)
        if result.exit_code != 0:
            return None, result.output
        try:
            return json.loads(result.output), None
        except json.JSONDecodeError as exc:
            return None, f"Invalid SSH JSON response: {exc}: {result.output[:1000]}"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if not command or not isinstance(command, str):
            return ExecuteResponse(output="Error: Command must be a non-empty string.", exit_code=1, truncated=False)
        script = f"cd {shlex.quote(self.workspace)} && {command}"
        return self._run_remote(script, timeout=timeout)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        import asyncio

        return await asyncio.to_thread(self.execute, command, timeout=timeout)

    def ls(self, path: str) -> LsResult:
        try:
            target = self._remote_path(path)
        except ValueError as exc:
            return LsResult(error=str(exc), entries=[])
        data, error = self._python_json(
            r'''
import datetime, json, os
path = os.environ["TARGET"]
entries = []
if os.path.isdir(path):
    for name in os.listdir(path):
        child = os.path.join(path, name)
        try:
            st = os.stat(child)
            is_dir = os.path.isdir(child)
            entries.append({
                "path": "/" + os.path.relpath(child, os.environ["WORKSPACE"]).replace(os.sep, "/") + ("/" if is_dir else ""),
                "is_dir": is_dir,
                "size": 0 if is_dir else int(st.st_size),
                "modified_at": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(),
            })
        except OSError:
            continue
print(json.dumps({"entries": entries}))
'''.replace('os.environ["TARGET"]', repr(target)).replace('os.environ["WORKSPACE"]', repr(self.workspace)),
            {},
        )
        if error:
            return LsResult(error=error, entries=[])
        return LsResult(entries=data.get("entries", []) if data else [])

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            target = self._remote_path(file_path)
        except ValueError as exc:
            return ReadResult(error=str(exc))
        data, error = self._python_json(
            r'''
import base64, json, os
path = TARGET
offset = OFFSET
limit = LIMIT
if not os.path.isfile(path):
    print(json.dumps({"error": f"File not found: {path}"}))
else:
    raw = open(path, "rb").read()
    try:
        text = raw.decode("utf-8")
        lines = text.splitlines(keepends=True)
        if offset >= len(lines) and lines:
            print(json.dumps({"error": f"Line offset {offset} exceeds file length ({len(lines)} lines)"}))
        else:
            print(json.dumps({"content": "".join(lines[offset:offset + limit]), "encoding": "utf-8"}))
    except UnicodeDecodeError:
        print(json.dumps({"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"}))
'''.replace("TARGET", repr(target)).replace("OFFSET", repr(int(offset))).replace("LIMIT", repr(int(limit))),
            {},
        )
        if error:
            return ReadResult(error=error)
        if not data:
            return ReadResult(error="Empty SSH response")
        if data.get("error"):
            return ReadResult(error=data["error"])
        return ReadResult(file_data=FileData(content=data.get("content", ""), encoding=data.get("encoding", "utf-8")))

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            target = self._remote_path(file_path)
        except ValueError as exc:
            return WriteResult(error=str(exc))
        payload = {"path": target, "content": content}
        data, error = self._python_json(
            r'''
import base64, json, os
payload = json.loads(base64.b64decode(os.environ["PAYLOAD"]))
path = payload["path"]
if os.path.exists(path):
    print(json.dumps({"error": f"Cannot write to {path} because it already exists."}))
else:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "w", encoding="utf-8", newline="").write(payload["content"])
    print(json.dumps({"path": path}))
''',
            payload,
        )
        if error:
            return WriteResult(error=error)
        if data and data.get("error"):
            return WriteResult(error=data["error"])
        return WriteResult(path=file_path)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        try:
            target = self._remote_path(file_path)
        except ValueError as exc:
            return EditResult(error=str(exc))
        payload = {"path": target, "old": old_string, "new": new_string, "replace_all": replace_all}
        data, error = self._python_json(
            r'''
import base64, json, os
p = json.loads(base64.b64decode(os.environ["PAYLOAD"]))
path = p["path"]
if not os.path.isfile(path):
    print(json.dumps({"error": f"Error: File '{path}' not found"}))
else:
    content = open(path, encoding="utf-8").read()
    old = p["old"].replace("\r\n", "\n").replace("\r", "\n")
    new = p["new"].replace("\r\n", "\n").replace("\r", "\n")
    count = content.count(old)
    if count == 0:
        print(json.dumps({"error": "old_string not found"}))
    elif count > 1 and not p["replace_all"]:
        print(json.dumps({"error": f"old_string occurs {count} times; set replace_all to true"}))
    else:
        content = content.replace(old, new, -1 if p["replace_all"] else 1)
        open(path, "w", encoding="utf-8", newline="").write(content)
        print(json.dumps({"occurrences": count if p["replace_all"] else 1}))
''',
            payload,
        )
        if error:
            return EditResult(error=error)
        if data and data.get("error"):
            return EditResult(error=data["error"])
        return EditResult(path=file_path, occurrences=int(data.get("occurrences", 1) if data else 1))

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        try:
            target = self._remote_path(path or "/")
        except ValueError as exc:
            return GrepResult(error=str(exc), matches=[])
        payload = {"root": target, "workspace": self.workspace, "pattern": pattern, "glob": glob}
        data, error = self._python_json(
            r'''
import base64, fnmatch, json, os
p = json.loads(base64.b64decode(os.environ["PAYLOAD"]))
matches = []
root = p["root"]
paths = [root] if os.path.isfile(root) else []
if os.path.isdir(root):
    for base, _, files in os.walk(root):
        for name in files:
            paths.append(os.path.join(base, name))
for file_path in paths:
    rel = os.path.relpath(file_path, p["workspace"]).replace(os.sep, "/")
    if p.get("glob") and not fnmatch.fnmatch(rel, p["glob"]):
        continue
    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, 1):
                if len(matches) >= 2000:
                    break
                if p["pattern"] in line:
                    matches.append({"path": "/" + rel, "line": i, "text": line.rstrip("\n")})
    except (OSError, UnicodeError):
        continue
print(json.dumps({"matches": matches}))
''',
            payload,
            timeout=30,
        )
        if error:
            return GrepResult(error=error, matches=[])
        return GrepResult(matches=data.get("matches", []) if data else [])

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        try:
            target = self._remote_path(path or "/")
        except ValueError as exc:
            return GlobResult(error=str(exc), matches=[])
        payload = {"root": target, "workspace": self.workspace, "pattern": pattern.lstrip("/")}
        data, error = self._python_json(
            r'''
import datetime, glob, json, os, base64
p = json.loads(base64.b64decode(os.environ["PAYLOAD"]))
items = []
for file_path in glob.glob(os.path.join(p["root"], p["pattern"]), recursive=True):
    if not os.path.isfile(file_path):
        continue
    rel = os.path.relpath(file_path, p["workspace"]).replace(os.sep, "/")
    try:
        st = os.stat(file_path)
        items.append({"path": "/" + rel, "is_dir": False, "size": int(st.st_size), "modified_at": datetime.datetime.fromtimestamp(st.st_mtime).isoformat()})
    except OSError:
        items.append({"path": "/" + rel, "is_dir": False})
print(json.dumps({"matches": items}))
''',
            payload,
            timeout=30,
        )
        if error:
            return GlobResult(error=error, matches=[])
        return GlobResult(matches=data.get("matches", []) if data else [])

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                target = self._remote_path(path)
            except ValueError:
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            payload = {"path": target, "content": base64.b64encode(content).decode("ascii")}
            _, error = self._python_json(
                r'''
import base64, json, os
p = json.loads(base64.b64decode(os.environ["PAYLOAD"]))
os.makedirs(os.path.dirname(p["path"]) or ".", exist_ok=True)
open(p["path"], "wb").write(base64.b64decode(p["content"]))
print(json.dumps({"ok": True}))
''',
                payload,
            )
            responses.append(FileUploadResponse(path=path, error="invalid_path" if error else None))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                target = self._remote_path(path)
            except ValueError:
                responses.append(FileDownloadResponse(path=path, error="invalid_path"))
                continue
            data, error = self._python_json(
                r'''
import base64, json, os
path = TARGET
if not os.path.exists(path):
    print(json.dumps({"error": "file_not_found"}))
elif os.path.isdir(path):
    print(json.dumps({"error": "is_directory"}))
else:
    print(json.dumps({"content": base64.b64encode(open(path, "rb").read()).decode("ascii")}))
'''.replace("TARGET", repr(target)),
                {},
            )
            if error:
                responses.append(FileDownloadResponse(path=path, error="invalid_path"))
            elif data and data.get("error"):
                responses.append(FileDownloadResponse(path=path, error=data["error"]))
            else:
                responses.append(FileDownloadResponse(path=path, content=base64.b64decode(data.get("content", ""))))
        return responses
