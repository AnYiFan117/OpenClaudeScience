"""Thread-scoped skill loading for 天玄·千枢科学发现平台."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable, NotRequired, Sequence, TypedDict

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.skills import (
    SkillMetadata,
    SkillsMiddleware,
    _parse_skill_metadata,
)

MAX_THREAD_SKILLS = 32
SKILL_READ_LINE_LIMIT = 10_000
AUTO_ATTACHMENT_SKILLS: dict[str, ThreadSkillItem] = {
    "pdf": {
        "key": "skills/pdf",
        "name": "pdf",
        "description": "Automatically loaded for PDF attachments.",
        "relativePath": "skills/pdf",
        "folderName": "pdf",
    },
    "docx": {
        "key": "skills/docx",
        "name": "docx",
        "description": "Automatically loaded for DOCX attachments.",
        "relativePath": "skills/docx",
        "folderName": "docx",
    },
    "xlsx": {
        "key": "skills/xlsx",
        "name": "xlsx",
        "description": "Automatically loaded for XLSX attachments.",
        "relativePath": "skills/xlsx",
        "folderName": "xlsx",
    },
    "pptx": {
        "key": "skills/pptx",
        "name": "pptx",
        "description": "Automatically loaded for PPTX attachments.",
        "relativePath": "skills/pptx",
        "folderName": "pptx",
    },
}
AUTO_ATTACHMENT_MIME_TYPES: dict[str, str] = {
    "application/pdf": "pdf",
    "application/msword": "docx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-powerpoint": "pptx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}
AUTO_ATTACHMENT_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".doc": "docx",
    ".docx": "docx",
    ".xls": "xlsx",
    ".xlsx": "xlsx",
    ".ppt": "pptx",
    ".pptx": "pptx",
}


class ThreadSkillItem(TypedDict, total=False):
    key: str
    name: str
    description: str
    relativePath: str
    folderName: str
    addedAt: int


class ThreadSkills(TypedDict):
    revision: int
    active: list[ThreadSkillItem]


class ThreadSkillState(AgentState):
    """State schema for thread-level active skills."""

    threadSkills: NotRequired[ThreadSkills | None]
    skills_metadata: NotRequired[Annotated[list[SkillMetadata], PrivateStateAttr]]
    skills_load_errors: NotRequired[Annotated[list[str], PrivateStateAttr]]
    thread_skills_loaded_revision: NotRequired[Annotated[int, PrivateStateAttr]]
    thread_skills_loaded_signature: NotRequired[Annotated[str, PrivateStateAttr]]


def _current_config() -> dict[str, Any]:
    try:
        return dict(get_config())
    except RuntimeError:
        return {}


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_thread_skills(value: Any) -> ThreadSkills:
    if not isinstance(value, dict):
        return {"revision": 0, "active": []}

    revision_value = value.get("revision")
    try:
        revision = int(revision_value)
    except (TypeError, ValueError):
        revision = 0
    revision = max(0, revision)

    raw_active = value.get("active")
    if not isinstance(raw_active, list):
        return {"revision": revision, "active": []}

    active: list[ThreadSkillItem] = []
    seen: set[str] = set()
    for raw_item in raw_active:
        if not isinstance(raw_item, dict):
            continue
        relative_path = _clean_string(raw_item.get("relativePath"))
        key = _clean_string(raw_item.get("key")) or relative_path
        skill_path = relative_path or key
        if skill_path is None or skill_path in seen:
            continue
        seen.add(skill_path)
        item: ThreadSkillItem = {
            "key": key or skill_path,
            "relativePath": skill_path,
        }
        for field in ("name", "description", "folderName"):
            cleaned = _clean_string(raw_item.get(field))
            if cleaned:
                item[field] = cleaned
        added_at = raw_item.get("addedAt")
        if isinstance(added_at, int):
            item["addedAt"] = added_at
        active.append(item)
        if len(active) >= MAX_THREAD_SKILLS:
            break

    return {"revision": revision, "active": active}


def _message_additional_kwargs(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        raw = message.get("additional_kwargs") or message.get("additionalKwargs")
        if isinstance(raw, dict):
            return raw
        raw_kwargs = message.get("kwargs")
        if isinstance(raw_kwargs, dict):
            nested = raw_kwargs.get("additional_kwargs") or raw_kwargs.get(
                "additionalKwargs"
            )
            if isinstance(nested, dict):
                return nested
        return {}

    raw = getattr(message, "additional_kwargs", None)
    return raw if isinstance(raw, dict) else {}


def _attachment_values(attachment: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "kind",
        "mimeType",
        "mime_type",
        "name",
        "workspacePath",
        "workspace_path",
        "extractedWorkspacePath",
        "extracted_workspace_path",
    ):
        value = attachment.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip().lower())
    return values


def _attachment_skill_names(attachment: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    values = _attachment_values(attachment)

    if str(attachment.get("kind") or "").lower() == "pdf":
        names.add("pdf")

    for value in values:
        mime_skill = AUTO_ATTACHMENT_MIME_TYPES.get(value)
        if mime_skill:
            names.add(mime_skill)

        for extension, extension_skill in AUTO_ATTACHMENT_EXTENSIONS.items():
            if value.endswith(extension):
                names.add(extension_skill)

    return names


def _infer_attachment_skills(state: dict[str, Any]) -> list[ThreadSkillItem]:
    inferred: list[ThreadSkillItem] = []
    seen: set[str] = set()
    messages = state.get("messages")
    if not isinstance(messages, list):
        return inferred

    for message in messages:
        additional_kwargs = _message_additional_kwargs(message)
        attachments = additional_kwargs.get("attachments")
        if not isinstance(attachments, list):
            continue
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            for skill_name in _attachment_skill_names(attachment):
                template = AUTO_ATTACHMENT_SKILLS.get(skill_name)
                if not template:
                    continue
                key = template["key"]
                if key in seen:
                    continue
                seen.add(key)
                inferred.append(dict(template))
    return inferred


def _merge_thread_skills(
    thread_skills: ThreadSkills,
    inferred_skills: list[ThreadSkillItem],
) -> ThreadSkills:
    active: list[ThreadSkillItem] = []
    seen: set[str] = set()

    for skill in [*thread_skills["active"], *inferred_skills]:
        relative_path = _clean_string(skill.get("relativePath"))
        key = _clean_string(skill.get("key")) or relative_path
        identity = relative_path or key
        if identity is None or identity in seen:
            continue
        seen.add(identity)
        active.append(skill)
        if len(active) >= MAX_THREAD_SKILLS:
            break

    return {"revision": thread_skills["revision"], "active": active}


def _thread_skill_signature(thread_skills: ThreadSkills) -> str:
    identities = []
    for skill in thread_skills["active"]:
        identity = _clean_string(skill.get("relativePath")) or _clean_string(
            skill.get("key")
        )
        if identity:
            identities.append(identity)
    return "|".join(sorted(set(identities)))


def _strip_thread_skills_update(update: Any) -> Any:
    if isinstance(update, dict):
        if "threadSkills" not in update:
            return update
        return {key: value for key, value in update.items() if key != "threadSkills"}

    if isinstance(update, list):
        filtered = [
            item
            for item in update
            if not (
                isinstance(item, tuple)
                and len(item) == 2
                and item[0] == "threadSkills"
            )
        ]
        return update if len(filtered) == len(update) else filtered

    if isinstance(update, tuple):
        filtered = tuple(
            item
            for item in update
            if not (
                isinstance(item, tuple)
                and len(item) == 2
                and item[0] == "threadSkills"
            )
        )
        return update if len(filtered) == len(update) else filtered

    return update


@dataclass
class ThreadSkillMiddleware(AgentMiddleware):
    """Expose skills selected for the current thread to model calls."""

    backend: Any
    root_dir: Path
    catalog_paths: Sequence[str]
    label: str = "天玄·千枢科学发现平台"

    state_schema = ThreadSkillState

    @property
    def name(self) -> str:
        return "ThreadSkillMiddleware"

    def __post_init__(self) -> None:
        self.root_dir = Path(self.root_dir).resolve()
        catalog_roots = []
        for catalog_path in self.catalog_paths:
            if not isinstance(catalog_path, str) or not catalog_path.strip():
                continue
            path = Path(catalog_path).expanduser()
            if not path.is_absolute():
                path = self.root_dir / path
            catalog_roots.append(path.resolve())
        if not catalog_roots:
            catalog_roots.append((self.root_dir / "skills").resolve())
        self._catalog_roots = tuple(catalog_roots)
        self._prompt = SkillsMiddleware(
            backend=self.backend,
            sources=[("skill://", self.label)],
        )

    def _get_backend(self, state: dict[str, Any], runtime: Any) -> BackendProtocol:
        if callable(self.backend):
            tool_runtime = ToolRuntime(
                state=state,
                context=getattr(runtime, "context", None),
                stream_writer=getattr(runtime, "stream_writer", None),
                store=getattr(runtime, "store", None),
                config=_current_config(),
                tool_call_id=None,
            )
            resolved = self.backend(tool_runtime)
            if resolved is None:
                raise AssertionError("ThreadSkillMiddleware requires a backend")
            return resolved
        return self.backend

    def _skill_lookup_aliases(self, skill: ThreadSkillItem) -> list[str]:
        aliases: list[str] = []
        for field in ("name", "folderName", "relativePath", "key"):
            value = _clean_string(skill.get(field))
            if not value:
                continue
            path_name = Path(value).name
            for alias in (value, path_name):
                if alias and alias not in aliases:
                    aliases.append(alias)
        return aliases

    def _resolve_skill_by_alias(self, skill: ThreadSkillItem) -> Path | None:
        for alias in self._skill_lookup_aliases(skill):
            for catalog_root in self._catalog_roots:
                candidate = (catalog_root / alias).resolve(strict=False)
                try:
                    candidate.relative_to(catalog_root)
                except ValueError:
                    continue
                if (candidate / "SKILL.md").is_file():
                    return candidate
        return None

    def _resolve_skill_dir(self, skill: ThreadSkillItem) -> Path | None:
        raw_path = _clean_string(skill.get("relativePath")) or _clean_string(
            skill.get("key")
        )
        if raw_path is None:
            return self._resolve_skill_by_alias(skill)
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = self.root_dir / path
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            return None

        for catalog_root in self._catalog_roots:
            try:
                resolved.relative_to(catalog_root)
                if (resolved / "SKILL.md").is_file():
                    return resolved
            except ValueError:
                continue
        return self._resolve_skill_by_alias(skill)

    def _load_skills(
        self,
        backend: BackendProtocol,
        thread_skills: ThreadSkills,
    ) -> tuple[list[SkillMetadata], list[str]]:
        loaded: dict[str, SkillMetadata] = {}
        errors: list[str] = []

        for skill in thread_skills["active"]:
            skill_dir = self._resolve_skill_dir(skill)
            if skill_dir is None:
                key = skill.get("key") or skill.get("relativePath") or "(unknown)"
                errors.append(f"Skill {key!r} is outside the configured skill catalogs.")
                continue

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                errors.append(f"Skill {skill_dir.as_posix()} does not contain SKILL.md.")
                continue

            skill_md_path = skill_md.as_posix()
            result = backend.read(skill_md_path, offset=0, limit=SKILL_READ_LINE_LIMIT)
            if result.error:
                errors.append(f"Cannot load {skill_md_path}: {result.error}")
                continue
            if result.file_data is None:
                errors.append(f"Cannot load {skill_md_path}: empty read response.")
                continue
            if result.file_data.get("encoding") != "utf-8":
                errors.append(f"Cannot load {skill_md_path}: SKILL.md is not text.")
                continue

            metadata = _parse_skill_metadata(
                result.file_data.get("content", ""),
                skill_md_path,
                skill_dir.name,
            )
            if metadata is None:
                errors.append(f"Cannot parse skill metadata from {skill_md_path}.")
                continue
            metadata = dict(metadata)
            metadata["path"] = f"skill://{metadata['name']}/SKILL.md"
            loaded[metadata["name"]] = metadata

        return list(loaded.values()), errors

    def before_model(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        configured_thread_skills = _normalize_thread_skills(state.get("threadSkills"))
        thread_skills = _merge_thread_skills(
            configured_thread_skills,
            _infer_attachment_skills(state),
        )
        revision = thread_skills["revision"]
        signature = _thread_skill_signature(thread_skills)
        if (
            state.get("thread_skills_loaded_revision") == revision
            and state.get("thread_skills_loaded_signature") == signature
            and "skills_metadata" in state
        ):
            return None

        if not thread_skills["active"]:
            return {
                "skills_metadata": [],
                "skills_load_errors": [],
                "thread_skills_loaded_revision": revision,
                "thread_skills_loaded_signature": signature,
            }

        backend = self._get_backend(state, runtime)
        skills, errors = self._load_skills(backend, thread_skills)
        return {
            "skills_metadata": skills,
            "skills_load_errors": errors,
            "thread_skills_loaded_revision": revision,
            "thread_skills_loaded_signature": signature,
        }

    async def abefore_model(
        self,
        state: dict[str, Any],
        runtime: Any,
    ) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    def _has_thread_skill_context(self, state: dict[str, Any]) -> bool:
        thread_skills = _normalize_thread_skills(state.get("threadSkills"))
        return bool(
            thread_skills["active"]
            or _infer_attachment_skills(state)
            or state.get("skills_metadata")
            or state.get("skills_load_errors")
        )

    def _strip_task_thread_skills(
        self,
        result: ToolMessage | Command[Any],
    ) -> ToolMessage | Command[Any]:
        if not isinstance(result, Command) or result.update is None:
            return result

        update = _strip_thread_skills_update(result.update)
        if update is result.update:
            return result

        return Command(
            graph=result.graph,
            update=update,
            resume=result.resume,
            goto=result.goto,
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        result = handler(request)
        if request.tool_call.get("name") != "task":
            return result
        return self._strip_task_thread_skills(result)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        if request.tool_call.get("name") != "task":
            return result
        return self._strip_task_thread_skills(result)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        if self._has_thread_skill_context(request.state or {}):
            request = self._prompt.modify_request(request)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if self._has_thread_skill_context(request.state or {}):
            request = self._prompt.modify_request(request)
        return await handler(request)
