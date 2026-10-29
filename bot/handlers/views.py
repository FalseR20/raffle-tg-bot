"""Shared participant-facing views."""

from __future__ import annotations

from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ApplicationStatus, RaffleStatus
from bot.db.models import Raffle, User
from bot.db.repositories import applications as apps_repo
from bot.db.repositories import organizations as orgs_repo
from bot.db.repositories import raffles as raffles_repo
from bot.domain.ordering import randomized_organizations
from bot.keyboards.participant import (
    channel_keyboard,
    consent_keyboard,
    draft_keyboard,
    organizations_keyboard,
    policy_keyboard,
)
from bot.services import notifications
from bot.services.raffles import close_due
from bot.services.settings import RuntimeSettings
from bot.services.users import has_valid_consent, operator_is_identified
from bot.texts import privacy, ru


async def send_consent(
    bot: Bot,
    chat_id: int,
    settings: RuntimeSettings,
    *,
    message_thread_id: int | None = None,
) -> None:
    """Show the legal notice before the consent button."""
    identified = operator_is_identified(settings)
    if not identified:
        await notifications.send_text(
            bot,
            chat_id,
            privacy.operator_missing_notice(),
            reply_markup=consent_keyboard(
                age_required=settings.require_age_confirmation,
                can_accept=False,
            ),
            message_thread_id=message_thread_id,
        )
        return
    await notifications.send_text(
        bot,
        chat_id,
        privacy.processing_notice(settings),
        message_thread_id=message_thread_id,
    )
    await notifications.send_text(
        bot,
        chat_id,
        privacy.rights_notice(settings),
        reply_markup=consent_keyboard(
            age_required=settings.require_age_confirmation,
            can_accept=True,
        ),
        message_thread_id=message_thread_id,
    )


async def send_policy(
    bot: Bot,
    chat_id: int,
    settings: RuntimeSettings,
    user: User,
    *,
    message_thread_id: int | None = None,
) -> None:
    pages = privacy.policy_pages(settings)
    markup = policy_keyboard(
        can_accept=operator_is_identified(settings) and not has_valid_consent(user, settings),
        age_required=settings.require_age_confirmation,
        has_publish_consent=user.publish_name_consent_at is not None,
    )
    for index, page in enumerate(pages):
        await notifications.send_text(
            bot,
            chat_id,
            page,
            reply_markup=markup if index == len(pages) - 1 else None,
            message_thread_id=message_thread_id,
        )


def raffle_channel(raffle: Raffle, settings: RuntimeSettings) -> tuple[str | None, str | None]:
    """Prefer the raffle channel, falling back to the global setting."""
    return (
        raffle.channel_url or settings.channel_url,
        raffle.channel_title or settings.channel_title,
    )


def append_channel_button(
    keyboard: InlineKeyboardMarkup, url: str | None, title: str | None
) -> None:
    if url:
        keyboard.inline_keyboard.append(
            [InlineKeyboardButton(text=f"📣 {title or 'Канал'}", url=url)]
        )


async def current_raffle(session: AsyncSession, now: datetime) -> Raffle | None:
    raffle = await raffles_repo.get_current(session)
    if raffle is not None and raffle.status is RaffleStatus.ACTIVE and raffle.end_at <= now:
        await close_due(session, raffle=raffle, now=now)
    return raffle


async def send_no_raffle(
    bot: Bot, chat_id: int, settings: RuntimeSettings, *, message_thread_id: int | None = None
) -> None:
    rows = [[InlineKeyboardButton(text="🏆 Мои результаты", callback_data="my:results")]]
    if settings.channel_url:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📣 {settings.channel_title or 'Канал'}", url=settings.channel_url
                )
            ]
        )
    await notifications.send_text(
        bot,
        chat_id,
        ru.no_active_raffle_text(settings.channel_title),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        message_thread_id=message_thread_id,
    )


async def send_closed(
    bot: Bot, chat_id: int, settings: RuntimeSettings, *, message_thread_id: int | None = None
) -> None:
    rows = [[InlineKeyboardButton(text="🎟 Мои заявки", callback_data="my:list")]]
    if settings.channel_url:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📣 {settings.channel_title or 'Канал'}", url=settings.channel_url
                )
            ]
        )
    await notifications.send_text(
        bot,
        chat_id,
        ru.closed_text(settings.channel_title),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        message_thread_id=message_thread_id,
    )


async def send_start_screen(
    session: AsyncSession,
    bot: Bot,
    *,
    user: User,
    settings: RuntimeSettings,
    chat_id: int,
    now: datetime,
    message_thread_id: int | None = None,
) -> None:
    raffle = await current_raffle(session, now)
    if raffle is None:
        raffle = await raffles_repo.latest_scheduled(session)
        if raffle is None:
            await send_no_raffle(bot, chat_id, settings, message_thread_id=message_thread_id)
            return

    if raffle.status is RaffleStatus.SCHEDULED:
        channel_url, channel_title = raffle_channel(raffle, settings)
        await notifications.send_text(
            bot,
            chat_id,
            ru.scheduled_raffle_text(raffle, settings.timezone),
            reply_markup=channel_keyboard(channel_url, channel_title),
            message_thread_id=message_thread_id,
        )
        return
    if raffle.status is not RaffleStatus.ACTIVE:
        await send_closed(bot, chat_id, settings, message_thread_id=message_thread_id)
        return

    consent = has_valid_consent(user, settings)
    if not consent:
        await send_consent(bot, chat_id, settings, message_thread_id=message_thread_id)
        return

    draft = await apps_repo.latest_draft(session, raffle.id, user.id)
    if draft is not None:
        await notifications.send_text(
            bot,
            chat_id,
            "У вас есть незавершённая заявка\\. Продолжить её или начать новую?",
            reply_markup=draft_keyboard(),
            message_thread_id=message_thread_id,
        )
        return

    organizations = await raffles_repo.enabled_organizations(session, raffle.id)
    organizations = [
        organization
        for organization in organizations
        if await orgs_repo.has_active_payment_methods(session, organization.id)
    ]
    organizations = randomized_organizations(organizations, user_id=user.id, raffle_id=raffle.id)
    keyboard = organizations_keyboard(
        [(organization.id, organization.name) for organization in organizations]
    )
    append_channel_button(keyboard, *raffle_channel(raffle, settings))
    text = ru.active_raffle_text(raffle, settings.timezone, has_consent=True)
    await notifications.send_text(
        bot,
        chat_id,
        text,
        reply_markup=keyboard,
        message_thread_id=message_thread_id,
    )

    pending = [
        application
        for application in await apps_repo.open_for_user(session, raffle.id, user.id)
        if application.status
        in {ApplicationStatus.PENDING_REVIEW, ApplicationStatus.REVISION_REQUESTED}
    ]
    if pending:
        lines = "\n\n".join(_pending_line(application) for application in pending[:5])
        await notifications.send_text(
            bot,
            chat_id,
            "ℹ️ У вас есть заявки, ожидающие проверки:\n\n"
            f"{lines}\n\nНовая заявка — это отдельное пожертвование и отдельный чек\\.",
            message_thread_id=message_thread_id,
        )


def _pending_line(application) -> str:
    return (
        f"№{ru.esc(application.public_id)} · {ru.esc(application.organization.name)} · "
        f"{ru.esc(ru.STATUS_LABELS[application.status.value])}"
    )
