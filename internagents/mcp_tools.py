"""Load LangChain tools from configured MCP servers."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable

from internagents.mcp_config import McpConfig, McpServerConfig, load_mcp_config


LOGGER = logging.getLogger(__name__)
_ACTIVE_MCP_CLIENTS: list[Any] = []
_LAST_MCP_STATUSES: list["McpServerStatus"] = []
_MCP_TOOLS_CACHE_KEY: tuple[Any, ...] | None = None
_MCP_TOOLS_CACHE: list[Any] | None = None


@dataclass(frozen=True)
class McpServerStatus:
    name: str
    status: str
    source: str
    tool_names: tuple[str, ...] = ()
    error: str | None = None


def load_configured_mcp_tools(
    agent_config: dict[str, Any],
    *,
    root_dir: Path,
) -> list[Any]:
    """Load tools from configured MCP servers.

    No MCP config means no-op. Individual MCP server failures are recorded and
    logged, but they do not prevent the 天玄·千枢科学发现平台 graph from starting.
    """

    global _MCP_TOOLS_CACHE_KEY, _MCP_TOOLS_CACHE

    config = load_mcp_config(agent_config, root_dir=root_dir)
    if config.errors:
        for error in config.errors:
            LOGGER.warning("MCP config warning: %s", error)
    if not config.servers:
        _set_statuses([])
        return []

    cache_key = _config_cache_key(config)
    if _MCP_TOOLS_CACHE_KEY == cache_key and _MCP_TOOLS_CACHE is not None:
        return list(_MCP_TOOLS_CACHE)

    try:
        tools, statuses = _run_async(_load_mcp_tools_async(config))
    except Exception as exc:  # pragma: no cover - defensive startup guard
        LOGGER.warning("Failed to load MCP tools: %s", exc)
        _set_statuses(
            [
                McpServerStatus(
                    name=name,
                    status="error",
                    source=server.source,
                    error=str(exc),
                )
                for name, server in config.servers.items()
            ]
        )
        return []

    _set_statuses(statuses)
    _MCP_TOOLS_CACHE_KEY = cache_key
    _MCP_TOOLS_CACHE = list(tools)
    return tools


def get_last_mcp_statuses() -> tuple[McpServerStatus, ...]:
    return tuple(_LAST_MCP_STATUSES)


async def _load_mcp_tools_async(
    config: McpConfig,
) -> tuple[list[Any], list[McpServerStatus]]:
    tools: list[Any] = []
    statuses: list[McpServerStatus] = []
    seen_tool_names: set[str] = set()

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        message = "langchain-mcp-adapters is required when MCP servers are configured."
        return [], [
            McpServerStatus(
                name=name,
                status="error",
                source=server.source,
                error=message,
            )
            for name, server in config.servers.items()
        ]

    for name, server in config.servers.items():
        try:
            client = MultiServerMCPClient({name: server.to_adapter_config()})
            server_tools = await client.get_tools()
            server_tools = _filter_tools(server_tools, server)

            accepted_tools = []
            skipped_duplicates: list[str] = []
            for tool in server_tools:
                tool_name = getattr(tool, "name", None)
                if not isinstance(tool_name, str) or not tool_name:
                    continue
                if tool_name in seen_tool_names:
                    skipped_duplicates.append(tool_name)
                    continue
                seen_tool_names.add(tool_name)
                accepted_tools.append(tool)

            _ACTIVE_MCP_CLIENTS.append(client)
            tools.extend(accepted_tools)
            error = None
            status = "ok"
            if skipped_duplicates:
                error = "Skipped duplicate MCP tools: " + ", ".join(skipped_duplicates)
                LOGGER.warning("MCP server %s: %s", name, error)
            statuses.append(
                McpServerStatus(
                    name=name,
                    status=status,
                    source=server.source,
                    tool_names=tuple(
                        getattr(tool, "name")
                        for tool in accepted_tools
                        if isinstance(getattr(tool, "name", None), str)
                    ),
                    error=error,
                )
            )
            LOGGER.info("Loaded %d MCP tools from %s", len(accepted_tools), name)
        except Exception as exc:
            statuses.append(
                McpServerStatus(
                    name=name,
                    status="error",
                    source=server.source,
                    error=str(exc),
                )
            )
            LOGGER.warning("Failed to load MCP server %s: %s", name, exc)

    return tools, statuses


def _filter_tools(tools: list[Any], server: McpServerConfig) -> list[Any]:
    if not server.allowed_tools and not server.disabled_tools:
        return tools

    filtered: list[Any] = []
    for tool in tools:
        name = getattr(tool, "name", None)
        if not isinstance(name, str) or not name:
            continue

        if server.allowed_tools:
            if _matches_tool_filter(name, server.name, server.allowed_tools):
                filtered.append(tool)
            continue

        if server.disabled_tools and _matches_tool_filter(
            name,
            server.name,
            server.disabled_tools,
        ):
            continue
        filtered.append(tool)

    return filtered


def _matches_tool_filter(
    tool_name: str,
    server_name: str,
    patterns: tuple[str, ...],
) -> bool:
    names = (tool_name, f"{server_name}_{tool_name}")
    for pattern in patterns:
        for name in names:
            if fnmatch.fnmatchcase(name, pattern):
                return True
    return False


def _run_async(awaitable: Awaitable[Any]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result: Any = None
    error: BaseException | None = None

    def runner() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(awaitable)
        except BaseException as exc:  # pragma: no cover - mirrors asyncio.run
            error = exc

    thread = threading.Thread(target=runner, name="internagents-mcp-loader", daemon=True)
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result


def _set_statuses(statuses: list[McpServerStatus]) -> None:
    _LAST_MCP_STATUSES.clear()
    _LAST_MCP_STATUSES.extend(statuses)


def _config_cache_key(config: McpConfig) -> tuple[Any, ...]:
    return tuple(
        (
            name,
            server.transport,
            server.url,
            server.command,
            server.args,
            tuple(sorted(server.env.items())),
            tuple(sorted(server.headers.items())),
            server.allowed_tools,
            server.disabled_tools,
        )
        for name, server in sorted(config.servers.items())
    )
