"""Runtime date/time context injection for 天玄·千枢科学发现平台."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python 3.11+ provides zoneinfo.
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment]


TIMEZONE_ENV = "INTERNAGENTS_TIMEZONE"
Clock = Callable[[tzinfo], datetime]


def _default_now(tz: tzinfo) -> datetime:
    return datetime.now(tz)


def _local_timezone() -> tzinfo:
    return datetime.now().astimezone().tzinfo or timezone.utc


def _fixed_offset_timezone(value: str) -> tzinfo | None:
    match = re.fullmatch(r"UTC?([+-])(\d{1,2})(?::?(\d{2}))?", value.strip(), re.I)
    if not match:
        return None
    sign, hours, minutes = match.groups()
    delta = timedelta(hours=int(hours), minutes=int(minutes or "0"))
    if sign == "-":
        delta = -delta
    return timezone(delta)


def _resolve_timezone(timezone_name: str | None = None) -> tuple[tzinfo, str | None]:
    name = timezone_name or os.getenv(TIMEZONE_ENV)
    if name and name.strip():
        normalized = name.strip()
        if normalized.upper() == "UTC":
            return timezone.utc, "UTC"
        if ZoneInfo is not None:
            try:
                return ZoneInfo(normalized), normalized
            except ZoneInfoNotFoundError:
                pass
        fixed = _fixed_offset_timezone(normalized)
        if fixed is not None:
            return fixed, normalized
    return _local_timezone(), None


def _format_utc_offset(dt: datetime) -> str:
    offset = dt.utcoffset()
    if offset is None:
        return "UTC+00:00"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _weekday_name(dt: datetime) -> str:
    return [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ][dt.weekday()]


def render_runtime_date_context(
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
    clock: Clock = _default_now,
) -> str:
    tz, configured_label = _resolve_timezone(timezone_name)
    current = now.astimezone(tz) if now is not None else clock(tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    offset = _format_utc_offset(current)
    tz_name = current.tzname()
    label_parts = [part for part in (configured_label, tz_name, offset) if part]
    timezone_label = " / ".join(dict.fromkeys(label_parts))

    return (
        "Runtime date context:\n"
        f"- Current date: {current:%Y-%m-%d} ({_weekday_name(current)})\n"
        f"- Current local time: {current:%H:%M:%S}\n"
        f"- Time zone: {timezone_label}\n"
        f"- Yesterday: {current - timedelta(days=1):%Y-%m-%d}\n"
        f"- Tomorrow: {current + timedelta(days=1):%Y-%m-%d}\n"
        "- Treat dates before the current date as past dates, and dates after the "
        "current date as future dates.\n"
        "- Resolve relative dates in any user language, such as today, yesterday, "
        "tomorrow, this week, last week, and recent, relative to the current date "
        "above.\n"
        "- Date/time context only gives the temporal reference point. For current "
        "facts such as market prices, news, sports, laws, or recent events, use "
        "available search tools before making factual claims."
    )


def _append_to_system_message(
    system_message: SystemMessage | None,
    text: str,
) -> SystemMessage:
    if system_message is None:
        return SystemMessage(content=text)
    existing = system_message.text
    content = f"{existing}\n\n{text}" if existing else text
    return SystemMessage(content=content)


@dataclass
class RuntimeDateContextMiddleware(AgentMiddleware):
    """Adds current date/time context to each model request."""

    timezone_name: str | None = None
    clock: Clock = field(default=_default_now, repr=False)

    @property
    def name(self) -> str:
        return "RuntimeDateContextMiddleware"

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        request = request.override(
            system_message=_append_to_system_message(
                request.system_message,
                render_runtime_date_context(
                    timezone_name=self.timezone_name,
                    clock=self.clock,
                ),
            )
        )
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        request = request.override(
            system_message=_append_to_system_message(
                request.system_message,
                render_runtime_date_context(
                    timezone_name=self.timezone_name,
                    clock=self.clock,
                ),
            )
        )
        return await handler(request)
