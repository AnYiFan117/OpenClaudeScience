"""MCP server configuration loading for 天玄·千枢科学发现平台."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
DISABLE_ENV = "INTERNAGENT_MCP_DISABLED"
CONFIG_FILE_ENV = "INTERNAGENT_MCP_CONFIG_FILE"
SUPPORTED_TRANSPORTS = {
    "stdio": "stdio",
    "sse": "sse",
    "http": "http",
    "streamable_http": "http",
    "streamable-http": "http",
    "streamablehttp": "http",
    "streamableHttp": "http",
}


class McpConfigError(ValueError):
    """Raised when an MCP configuration entry is invalid."""


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    transport: str
    source: str
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    allowed_tools: tuple[str, ...] = ()
    disabled_tools: tuple[str, ...] = ()

    def to_adapter_config(self) -> dict[str, Any]:
        if self.transport == "stdio":
            config: dict[str, Any] = {
                "transport": "stdio",
                "command": self.command,
            }
            if self.args:
                config["args"] = list(self.args)
            if self.env:
                config["env"] = dict(self.env)
            return config

        config = {
            "transport": self.transport,
            "url": self.url,
        }
        if self.headers:
            config["headers"] = dict(self.headers)
        return config


@dataclass(frozen=True)
class McpConfig:
    servers: dict[str, McpServerConfig]
    sources: tuple[str, ...]
    errors: tuple[str, ...] = ()


def load_mcp_config(
    agent_config: dict[str, Any] | None,
    *,
    root_dir: Path,
    home_dir: Path | None = None,
) -> McpConfig:
    """Load MCP config from official-style discovery locations and overrides."""

    if _env_flag(DISABLE_ENV):
        return McpConfig(servers={}, sources=())

    agent_config = agent_config or {}
    mcp_section = agent_config.get("mcp")
    if isinstance(mcp_section, dict) and mcp_section.get("enabled") is False:
        return McpConfig(servers={}, sources=())

    home_dir = home_dir or Path.home()
    sources: list[Path] = [
        home_dir / ".deepagents" / ".mcp.json",
        root_dir / ".deepagents" / ".mcp.json",
        root_dir / ".mcp.json",
    ]

    if isinstance(mcp_section, dict):
        config_file = mcp_section.get("config_file") or mcp_section.get("configFile")
        if isinstance(config_file, str) and config_file.strip():
            sources.append(_resolve_path(config_file.strip(), root_dir))

    env_config_file = os.getenv(CONFIG_FILE_ENV)
    if env_config_file and env_config_file.strip():
        sources.append(_resolve_path(env_config_file.strip(), root_dir))

    servers: dict[str, McpServerConfig] = {}
    loaded_sources: list[str] = []
    errors: list[str] = []

    for source in sources:
        if not source.exists():
            continue
        loaded_sources.append(str(source))
        try:
            raw_config = _read_json(source)
            parsed_servers, parsed_errors = _parse_mcp_servers(
                raw_config,
                source=str(source),
            )
            servers.update(parsed_servers)
            errors.extend(parsed_errors)
        except McpConfigError as exc:
            errors.append(str(exc))

    inline_config = _inline_mcp_config(agent_config)
    if inline_config is not None:
        loaded_sources.append("deepagent.config.json:mcp")
        try:
            parsed_servers, parsed_errors = _parse_mcp_servers(
                inline_config,
                source="deepagent.config.json:mcp",
            )
            servers.update(parsed_servers)
            errors.extend(parsed_errors)
        except McpConfigError as exc:
            errors.append(str(exc))

    return McpConfig(
        servers=servers,
        sources=tuple(loaded_sources),
        errors=tuple(errors),
    )


def _inline_mcp_config(agent_config: dict[str, Any]) -> dict[str, Any] | None:
    mcp_section = agent_config.get("mcp")
    if isinstance(mcp_section, dict):
        if isinstance(mcp_section.get("mcpServers"), dict):
            return {"mcpServers": mcp_section["mcpServers"]}
        if isinstance(mcp_section.get("servers"), dict):
            return {"mcpServers": mcp_section["servers"]}

    if isinstance(agent_config.get("mcpServers"), dict):
        return {"mcpServers": agent_config["mcpServers"]}

    return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise McpConfigError(f"Invalid MCP config JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise McpConfigError(
            f"Invalid MCP config at {path}: top-level value must be an object."
        )
    return data


def _parse_mcp_servers(
    raw_config: dict[str, Any],
    *,
    source: str,
) -> tuple[dict[str, McpServerConfig], tuple[str, ...]]:
    raw_servers = raw_config.get("mcpServers")
    if not isinstance(raw_servers, dict):
        raise McpConfigError(
            f"Invalid MCP config at {source}: missing object field 'mcpServers'."
        )

    servers: dict[str, McpServerConfig] = {}
    errors: list[str] = []
    for name, raw_server in raw_servers.items():
        try:
            servers[str(name)] = _parse_server(str(name), raw_server, source=source)
        except McpConfigError as exc:
            errors.append(str(exc))

    return servers, tuple(errors)


def _parse_server(name: str, raw_server: Any, *, source: str) -> McpServerConfig:
    if not SERVER_NAME_RE.fullmatch(name):
        raise McpConfigError(
            f"Invalid MCP server {name!r} at {source}: name must match [A-Za-z0-9_-]+."
        )
    if not isinstance(raw_server, dict):
        raise McpConfigError(
            f"Invalid MCP server {name!r} at {source}: value must be an object."
        )

    raw_transport = raw_server.get("transport", raw_server.get("type"))
    if raw_transport is None:
        raw_transport = "stdio" if raw_server.get("command") else "http"
    if not isinstance(raw_transport, str):
        raise McpConfigError(
            f"Invalid MCP server {name!r} at {source}: transport must be a string."
        )

    transport = SUPPORTED_TRANSPORTS.get(raw_transport)
    if transport is None:
        raise McpConfigError(
            f"Invalid MCP server {name!r} at {source}: unsupported transport {raw_transport!r}."
        )

    if raw_server.get("auth") is not None:
        raise McpConfigError(
            f"Invalid MCP server {name!r} at {source}: auth is not implemented yet; use headers."
        )

    allowed_tools = _string_tuple(
        raw_server.get("allowedTools"),
        field_name="allowedTools",
    )
    disabled_tools = _string_tuple(
        raw_server.get("disabledTools"),
        field_name="disabledTools",
    )
    if allowed_tools and disabled_tools:
        raise McpConfigError(
            f"Invalid MCP server {name!r} at {source}: allowedTools and disabledTools are mutually exclusive."
        )

    if transport == "stdio":
        command = raw_server.get("command")
        if not isinstance(command, str) or not command.strip():
            raise McpConfigError(
                f"Invalid MCP server {name!r} at {source}: stdio servers require command."
            )
        if raw_server.get("url"):
            raise McpConfigError(
                f"Invalid MCP server {name!r} at {source}: stdio servers cannot also set url."
            )
        args = _string_tuple(
            raw_server.get("args"),
            field_name="args",
            allow_empty=True,
        )
        env = _string_dict(raw_server.get("env"), field_name="env", expand_env=False)
        return McpServerConfig(
            name=name,
            transport="stdio",
            source=source,
            command=command.strip(),
            args=args,
            env=env,
            allowed_tools=allowed_tools,
            disabled_tools=disabled_tools,
        )

    url = raw_server.get("url")
    if not isinstance(url, str) or not url.strip():
        raise McpConfigError(
            f"Invalid MCP server {name!r} at {source}: {transport} servers require url."
        )
    if raw_server.get("command"):
        raise McpConfigError(
            f"Invalid MCP server {name!r} at {source}: remote servers cannot also set command."
        )
    headers = _string_dict(raw_server.get("headers"), field_name="headers", expand_env=True)
    return McpServerConfig(
        name=name,
        transport=transport,
        source=source,
        url=url.strip(),
        headers=headers,
        allowed_tools=allowed_tools,
        disabled_tools=disabled_tools,
    )


def _string_tuple(
    value: Any,
    *,
    field_name: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or (not value and not allow_empty):
        raise McpConfigError(f"{field_name} must be a non-empty string array when set.")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise McpConfigError(f"{field_name} must contain only non-empty strings.")
        items.append(item.strip())
    return tuple(items)


def _string_dict(value: Any, *, field_name: str, expand_env: bool) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise McpConfigError(f"{field_name} must be an object when set.")
    result: dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key.strip():
            raise McpConfigError(f"{field_name} keys must be non-empty strings.")
        if not isinstance(raw, str):
            raise McpConfigError(f"{field_name}.{key} must be a string.")
        result[key] = _expand_env(raw) if expand_env else raw
    return result


def _expand_env(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        env_value = os.getenv(name)
        if env_value is None:
            raise McpConfigError(f"Missing environment variable {name!r} for MCP header.")
        return env_value

    return ENV_VAR_RE.sub(replace, value)


def _env_flag(name: str) -> bool:
    value = os.getenv(name)
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def _resolve_path(value: str, root_dir: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return root_dir / path
