"""Start, consent and general participant commands."""

from __future__ import annotations

from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.db.models import User
from bot.handlers.views import send_start_screen
from bot.keyboards.participant import consent_keyboard, publish_consent_keyboard
from bot.services import invites as invites_service
from bot.services import notifications
from bot.services import users as users_service
from bot.services.errors import ServiceError
from bot.services.permissions import Permissions
from bot.services.settings import RuntimeSettings
from bot.texts import ru
from bot.texts.markdown import escape_markdown_v2, escape_url

router = Router(name="common")


async def _send_consent(bot: Bot, chat_id: int, settings: RuntimeSettings) -> None:
    await notifications.send_text(
        bot,
        chat_id,
        ru.consent_text(
            rules_url=settings.rules_url,
            privacy_url=settings.privacy_url,
            age_required=settings.require_age_confirmation,
            rules_version=settings.rules_version,
            privacy_version=settings.privacy_version,
        ),
        reply_markup=consent_keyboard(age_required=settings.require_age_confirmation),
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    bot: Bot,
    session: AsyncSession,
    user: User,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
    now: datetime,
    state: FSMContext,
) -> None:
    await state.clear()
    deep_link = (command.args or "").strip()
    if deep_link.startswith("inv_"):
        await _redeem_invite(
            message, bot, session, user, permissions, runtime_settings, now, deep_link[4:]
        )
        return

    await send_start_screen(
        session,
        bot,
        user=user,
        settings=runtime_settings,
        chat_id=message.chat.id,
        now=now,
    )


async def _redeem_invite(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user: User,
    permissions: Permissions,
    settings: RuntimeSettings,
    now: datetime,
    token: str,
) -> None:
    try:
        redeemed = await invites_service.redeem_invite(
            session, token=token, user_id=user.id, now=now
        )
    except ServiceError as exc:
        await notifications.send_text(bot, message.chat.id, escape_markdown_v2(exc.user_message))
        return

    if redeemed.role.value == "organizer":
        role_text = "организатора"
    else:
        org = redeemed.invite.organization
        role_text = f"представителя организации «{org.name if org else ''}»"
    await session.commit()
    await notifications.send_text(
        bot,
        message.chat.id,
        f"✅ Вам выдана роль {escape_markdown_v2(role_text)}\\.\n\n"
        "Откройте /admin, чтобы перейти в админ\\-панель\\.",
    )
    if not users_service.has_valid_consent(user, settings):
        await _send_consent(bot, message.chat.id, settings)
    else:
        await send_start_screen(
            session,
            bot,
            user=user,
            settings=settings,
            chat_id=message.chat.id,
            now=now,
        )


@router.callback_query(F.data == "consent:accept")
async def on_consent_accept(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    runtime_settings: RuntimeSettings,
    now: datetime,
) -> None:
    await users_service.accept_consent(
        session, user, settings=runtime_settings, now=now, publish_name=None
    )
    await session.commit()
    await callback.answer("Согласие сохранено")
    if callback.message:
        await notifications.send_text(
            bot,
            callback.message.chat.id,
            "Можно ли опубликовать ваше имя, если вы выиграете?\n\n"
            "По умолчанию публикуется только номер билета\\. Это необязательный выбор\\.",
            reply_markup=publish_consent_keyboard(),
        )


@router.callback_query(F.data == "consent:cancel")
async def on_consent_cancel(
    callback: CallbackQuery, bot: Bot, runtime_settings: RuntimeSettings
) -> None:
    await callback.answer()
    if callback.message:
        await notifications.send_text(
            bot,
            callback.message.chat.id,
            "Без согласия с правилами и политикой конфиденциальности участие невозможно\\.",
        )


@router.callback_query(F.data.in_({"publish:yes", "publish:no"}))
async def on_publish_choice(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    runtime_settings: RuntimeSettings,
    now: datetime,
) -> None:
    consent = callback.data == "publish:yes"
    await users_service.set_publish_name(session, user.id, consent=consent, now=now)
    await session.commit()
    await callback.answer("Сохранено")
    if callback.message:
        await send_start_screen(
            session,
            bot,
            user=user,
            settings=runtime_settings,
            chat_id=callback.message.chat.id,
            now=now,
        )


@router.message(Command("help"))
async def cmd_help(
    message: Message,
    bot: Bot,
    runtime_settings: RuntimeSettings,
) -> None:
    text = (
        "ℹ️ *Помощь*\n\n"
        "Выберите организацию, переведите пожертвование напрямую ей и пришлите чек\\. "
        "После проверки вы получите номера билетов\\.\n\n"
        "Билеты считаются по общей сумме всех ваших подтверждённых пожертвований "
        "в розыгрыше\\.\n\n"
        "Команды: /start — участие, /my — мои заявки\\."
    )
    if runtime_settings.support_contact:
        text += f"\n\nПоддержка: {escape_markdown_v2(runtime_settings.support_contact)}"
    await notifications.send_text(bot, message.chat.id, text)


@router.message(Command("rules"))
async def cmd_rules(message: Message, runtime_settings: RuntimeSettings, bot: Bot) -> None:
    await notifications.send_text(
        bot,
        message.chat.id,
        "Правила розыгрыша и политика конфиденциальности:\n\n"
        f"[Правила]({escape_url(runtime_settings.rules_url)})\n"
        f"[Политика конфиденциальности]({escape_url(runtime_settings.privacy_url)})",
    )


@router.message(Command("chatid"))
async def cmd_chat_id(
    message: Message,
    bot: Bot,
    permissions: Permissions,
    env_settings: Settings,
) -> None:
    if not permissions.can_manage_raffles:
        return
    thread_id = message.message_thread_id
    text = (
        f"🆔 Chat ID: `{message.chat.id}`\n"
        f"🧵 Thread ID: `{thread_id if thread_id is not None else '—'}`"
    )
    await notifications.send_text(bot, message.chat.id, text)
