"""User and consent service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.db.repositories import users as users_repo
from bot.services.errors import ValidationError
from bot.services.settings import RuntimeSettings
from bot.texts.markdown import escape_markdown_v2


@dataclass(frozen=True)
class TelegramProfile:
    user_id: int
    username: str | None
    first_name: str
    last_name: str | None
    language_code: str | None


async def touch_user(session: AsyncSession, profile: TelegramProfile, *, now: datetime) -> User:
    """Record the technical id without storing the nickname or name."""
    return await users_repo.upsert(
        session,
        user_id=profile.user_id,
        username=None,
        first_name="",
        last_name=None,
        language_code=None,
        seen_at=now,
        update_profile=False,
    )


async def remember_profile(
    session: AsyncSession, profile: TelegramProfile, *, now: datetime
) -> User:
    """Store the Telegram name on the already loaded user.

    A second SQL upsert would race the in-memory row: the pending consent
    update could flush the empty nickname back over the stored one.
    """
    user = await users_repo.get(session, profile.user_id)
    if user is None:
        return await users_repo.upsert(
            session,
            user_id=profile.user_id,
            username=profile.username,
            first_name=profile.first_name,
            last_name=profile.last_name,
            language_code=profile.language_code,
            seen_at=now,
        )
    user.username = profile.username
    user.first_name = profile.first_name or ""
    user.last_name = profile.last_name
    user.language_code = profile.language_code
    user.last_seen_at = now
    await session.flush()
    return user


async def forget_profile(session: AsyncSession, user: User) -> User:
    return await users_repo.clear_profile(session, user)


def operator_is_identified(settings: RuntimeSettings) -> bool:
    name = (settings.operator_name or "").strip()
    address = (settings.operator_address or "").strip()
    return bool(name and address)


def has_valid_consent(user: User, settings: RuntimeSettings) -> bool:
    rules_ok = user.rules_accepted_at is not None and user.rules_version == settings.rules_version
    privacy_ok = (
        user.privacy_accepted_at is not None and user.privacy_version == settings.privacy_version
    )
    age_ok = not settings.require_age_confirmation or user.age_confirmed_at is not None
    return rules_ok and privacy_ok and age_ok


def consent_missing_reason(user: User, settings: RuntimeSettings) -> str | None:
    if user.rules_accepted_at is None or user.rules_version != settings.rules_version:
        return "rules"
    if user.privacy_accepted_at is None or user.privacy_version != settings.privacy_version:
        return "privacy"
    if settings.require_age_confirmation and user.age_confirmed_at is None:
        return "age"
    return None


async def accept_consent(
    session: AsyncSession,
    user: User,
    *,
    settings: RuntimeSettings,
    now: datetime,
    publish_name: bool | None,
    profile: TelegramProfile | None = None,
) -> None:
    if not operator_is_identified(settings):
        raise ValidationError(
            "Организатор ещё не указал имя и адрес. Согласие пока нельзя сохранить."
        )
    await users_repo.record_consent(
        session,
        user,
        rules_version=settings.rules_version,
        privacy_version=settings.privacy_version,
        accepted_at=now,
        age_required=settings.require_age_confirmation,
        publish_name=publish_name,
    )
    if profile is not None:
        await remember_profile(session, profile, now=now)


async def set_publish_name(
    session: AsyncSession, user_id: int, *, consent: bool, now: datetime
) -> None:
    await users_repo.set_publish_name_consent(session, user_id, consent=consent, at=now)


def display_name(user: User) -> str:
    full = " ".join(part for part in (user.first_name, user.last_name) if part).strip()
    return full or f"ID {user.id}"


def mention(user: User) -> str:
    """Return a MarkdownV2-safe mention."""
    if user.username:
        return escape_markdown_v2(f"@{user.username}")
    full_name = display_name(user)
    return f"[{escape_markdown_v2(full_name)}](tg://user?id={user.id})"
