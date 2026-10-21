"""Time helpers. Database values are UTC, UI values use the configured timezone."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_timezone(value: datetime, tz: ZoneInfo) -> datetime:
    return ensure_utc(value).astimezone(tz)


def format_datetime(value: datetime | None, tz: ZoneInfo) -> str:
    if value is None:
        return "—"
    return to_timezone(value, tz).strftime("%d.%m.%Y %H:%M")


def format_date(value: datetime | None, tz: ZoneInfo) -> str:
    if value is None:
        return "—"
    return to_timezone(value, tz).strftime("%d.%m.%Y")
