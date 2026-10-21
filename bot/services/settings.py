"""Runtime product settings.

Precedence: raffle-specific values, then the ``settings`` table, then
environment defaults (``bot.config.Settings``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import settings as settings_repo
from bot.domain.money import format_money
from bot.services.errors import ValidationError
from bot.utils.url import normalize_http_url, normalize_optional_http_url

SETTING_KEYS = (
    "channel_url",
    "channel_title",
    "rules_url",
    "privacy_url",
    "rules_version",
    "privacy_version",
    "support_contact",
    "management_log_chat_id",
    "max_donation",
    "receipt_retention_days",
    "confirm_two_step",
    "enable_random_draw",
    "timezone",
    "require_age_confirmation",
    "greeting_text",
)


@dataclass(frozen=True)
class RuntimeSettings:
    channel_url: str | None
    channel_title: str | None
    rules_url: str
    privacy_url: str
    rules_version: str
    privacy_version: str
    support_contact: str | None
    management_log_chat_id: int | None
    max_donation: Decimal
    default_ticket_price: Decimal
    default_min_amount: Decimal
    receipt_max_mb: int
    receipt_retention_days: int
    confirm_two_step: bool
    enable_random_draw: bool
    timezone_name: str
    require_age_confirmation: bool
    greeting_text: str | None

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)


def environment_defaults(env: Settings) -> RuntimeSettings:
    return RuntimeSettings(
        channel_url=env.channel_url,
        channel_title=env.channel_title,
        rules_url=env.rules_url,
        privacy_url=env.privacy_url,
        rules_version=env.rules_version,
        privacy_version=env.privacy_version,
        support_contact=env.support_contact,
        management_log_chat_id=env.management_log_chat_id,
        max_donation=env.max_donation,
        default_ticket_price=env.default_ticket_price,
        default_min_amount=env.default_min_amount,
        receipt_max_mb=env.receipt_max_mb,
        receipt_retention_days=env.receipt_retention_days,
        confirm_two_step=env.confirm_two_step,
        enable_random_draw=env.enable_random_draw,
        timezone_name=env.tz,
        require_age_confirmation=env.require_age_confirmation,
        greeting_text=None,
    )


def parse_setting_value(key: str, raw: str | int | bool | None) -> Any:
    """Parse and validate a raw admin input for a settings key."""
    if key in {"channel_title", "rules_url", "privacy_url", "rules_version", "privacy_version"}:
        if not isinstance(raw, str) or not raw.strip():
            raise ValidationError("Значение не может быть пустым.")
        if key in {"rules_url", "privacy_url"}:
            try:
                return normalize_http_url(raw)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        return raw.strip()

    if key == "channel_url":
        try:
            return normalize_optional_http_url(raw)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    if key in {"support_contact", "greeting_text"}:
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        return str(raw).strip()

    if key == "management_log_chat_id":
        if raw is None or (isinstance(raw, str) and not raw.strip()) or raw == "":
            return None
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError("ID чата должен быть числом.") from exc

    if key == "max_donation":
        try:
            value = Decimal(str(raw).replace(",", ".").strip())
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError("Максимальная сумма должна быть числом.") from exc
        if value <= 0:
            raise ValidationError("Максимальная сумма должна быть больше нуля.")
        return str(value.quantize(Decimal("0.01")))

    if key == "receipt_retention_days":
        try:
            days = int(str(raw))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Срок хранения должен быть целым числом дней.") from exc
        if days < 1:
            raise ValidationError("Срок хранения должен быть не меньше одного дня.")
        return days

    if key in {"confirm_two_step", "enable_random_draw", "require_age_confirmation"}:
        if isinstance(raw, bool):
            return raw
        normalized = str(raw).strip().lower()
        if normalized in {"1", "true", "да", "yes", "on"}:
            return True
        if normalized in {"0", "false", "нет", "no", "off"}:
            return False
        raise ValidationError("Ожидается «да» или «нет».")

    if key == "timezone":
        try:
            ZoneInfo(str(raw))
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValidationError("Неизвестная часовая зона.") from exc
        return str(raw)

    raise ValidationError(f"Неизвестная настройка: {key}")


def _coerce(key: str, value: Any, default: Any) -> Any:
    try:
        if key in {
            "confirm_two_step",
            "enable_random_draw",
            "require_age_confirmation",
        }:
            return bool(value)
        if key == "max_donation":
            return Decimal(str(value))
        if key in {"receipt_retention_days", "management_log_chat_id"}:
            return int(value) if value is not None else None
        return value
    except (TypeError, ValueError, InvalidOperation):
        return default


async def load_runtime_settings(session: AsyncSession, env: Settings) -> RuntimeSettings:
    defaults = environment_defaults(env)
    stored = await settings_repo.all_values(session)
    values = {
        key: _coerce(key, stored[key], getattr(defaults, key))
        for key in stored
        if key in SETTING_KEYS
    }
    if "timezone" in values:
        values["timezone_name"] = values.pop("timezone")
    return RuntimeSettings(**{**defaults.__dict__, **values})


async def update_setting(
    session: AsyncSession,
    *,
    key: str,
    raw: str | int | bool | None,
    actor_id: int,
    now: datetime,
) -> Any:
    value = parse_setting_value(key, raw)
    await settings_repo.set_value(session, key, value, updated_by=actor_id)
    await audit_repo.log(
        session,
        entity_type="settings",
        entity_id=key,
        actor_id=actor_id,
        action="setting_changed",
        payload={"key": key, "value": value},
        at=now,
    )
    return value


def setting_label(key: str) -> str:
    return {
        "channel_url": "Ссылка на канал",
        "channel_title": "Название канала",
        "rules_url": "Ссылка на правила",
        "privacy_url": "Ссылка на политику конфиденциальности",
        "rules_version": "Версия правил",
        "privacy_version": "Версия политики",
        "support_contact": "Контакт поддержки",
        "management_log_chat_id": "Чат управления",
        "max_donation": "Максимальная сумма заявки",
        "receipt_retention_days": "Срок хранения чеков (дней)",
        "confirm_two_step": "Двухшаговое подтверждение",
        "enable_random_draw": "Случайный розыгрыш",
        "timezone": "Часовая зона",
        "require_age_confirmation": "Подтверждение 18+",
        "greeting_text": "Приветствие",
    }.get(key, key)


def describe_setting(key: str, value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "да" if value else "нет"
    if key == "max_donation":
        return format_money(value)
    return str(value)
