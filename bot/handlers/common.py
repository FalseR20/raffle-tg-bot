"""Start, consent and general participant commands."""

from __future__ import annotations

import contextlib
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.db.models import User
from bot.handlers.views import send_consent, send_policy, send_start_screen
from bot.keyboards.participant import publish_consent_keyboard, withdraw_keyboard
from bot.services import invites as invites_service
from bot.services import notifications
from bot.services import privacy as privacy_service
from bot.services import users as users_service
from bot.services.cards import finalize_cancelled
from bot.services.errors import ServiceError
from bot.services.permissions import Permissions
from bot.services.settings import RuntimeSettings
from bot.services.storage import ReceiptStorage
from bot.texts import privacy
from bot.texts.markdown import escape_markdown_v2, escape_url

router = Router(name="common")


def _profile(callback: CallbackQuery) -> users_service.TelegramProfile | None:
    tg_user = callback.from_user
    if tg_user is None:
        return None
    return users_service.TelegramProfile(
        user_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
        last_name=tg_user.last_name,
        language_code=tg_user.language_code,
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
        await send_consent(bot, message.chat.id, settings)
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
    try:
        await users_service.accept_consent(
            session,
            user,
            settings=runtime_settings,
            now=now,
            publish_name=None,
            profile=_profile(callback),
        )
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await session.commit()
    await callback.answer("Согласие сохранено")
    if callback.message:
        await notifications.send_text(
            bot,
            callback.message.chat.id,
            "Можно ли опубликовать ваше имя, если вы выиграете?\n\n"
            "По умолчанию — только номер билета\\. Это необязательно\\.",
            reply_markup=publish_consent_keyboard(),
        )


@router.callback_query(F.data == "consent:policy")
async def on_consent_policy(
    callback: CallbackQuery,
    bot: Bot,
    user: User,
    runtime_settings: RuntimeSettings,
) -> None:
    await callback.answer()
    if callback.message:
        await send_policy(bot, callback.message.chat.id, runtime_settings, user)


@router.callback_query(F.data == "consent:withdraw")
async def on_withdraw_prompt(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    if callback.message:
        await notifications.send_text(
            bot,
            callback.message.chat.id,
            privacy.withdrawal_prompt(),
            reply_markup=withdraw_keyboard(),
        )


@router.callback_query(F.data == "consent:withdraw_no")
async def on_withdraw_cancel(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer("Согласие остаётся в силе")
    if callback.message:
        await notifications.send_text(bot, callback.message.chat.id, privacy.withdrawal_kept())


@router.callback_query(F.data == "consent:withdraw_yes")
async def on_withdraw_confirm(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    permissions: Permissions,
    storage: ReceiptStorage,
    now: datetime,
) -> None:
    result = await privacy_service.withdraw_consent(
        session,
        user=user,
        storage=storage,
        now=now,
        keep_profile=permissions.has_admin_panel,
    )
    await session.commit()
    await callback.answer("Согласие отозвано")
    for cancelled in result.cancelled:
        await _delete_card(bot, cancelled.card_chat_id, cancelled.card_message_id)
        await finalize_cancelled(bot, cancelled, storage)
    for chat_id, message_id in result.review_messages:
        await _delete_card(bot, chat_id, message_id)
    if callback.message:
        await notifications.send_text(
            bot,
            callback.message.chat.id,
            privacy.withdrawal_done(
                failed=result.receipts_failed,
                profile_cleared=result.profile_cleared,
            ),
        )


async def _delete_card(bot: Bot, chat_id: int | None, message_id: int | None) -> None:
    if chat_id is None or message_id is None:
        return
    with contextlib.suppress(TelegramBadRequest):
        await bot.delete_message(chat_id, message_id)


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
        "Выберите организацию, переведите пожертвование напрямую и пришлите чек\\. "
        "После проверки мы начислим билеты\\.\n\n"
        "Билеты суммируются по всем вашим подтверждённым пожертвованиям в розыгрыше\\.\n\n"
        "Команды: /start — участие, /my — мои заявки, /privacy — политика конфиденциальности\\."
    )
    if runtime_settings.support_contact:
        text += f"\n\nПоддержка: {escape_markdown_v2(runtime_settings.support_contact)}"
    await notifications.send_text(bot, message.chat.id, text)


@router.message(Command("rules"))
async def cmd_rules(message: Message, runtime_settings: RuntimeSettings, bot: Bot) -> None:
    await notifications.send_text(
        bot,
        message.chat.id,
        "Правила розыгрыша:\n\n"
        f"[Правила]({escape_url(runtime_settings.rules_url)})\n\n"
        "Политика конфиденциальности открывается командой /privacy\\.",
    )


@router.message(Command("privacy"))
async def cmd_privacy(
    message: Message,
    bot: Bot,
    user: User,
    runtime_settings: RuntimeSettings,
) -> None:
    await send_policy(bot, message.chat.id, runtime_settings, user)


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
