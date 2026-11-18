"""Participant → admin replies to bot-relayed messages."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.db.repositories import audit as audit_repo
from bot.keyboards.relay import admin_reply_button, cancel_button
from bot.services import notifications
from bot.services.users import display_name
from bot.states import ParticipantRelay
from bot.texts import ru

router = Router(name="relay")


@router.callback_query(F.data.regexp(r"^relay:reply:\d+:\d+$"))
async def on_start_reply(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
) -> None:
    if callback.message is None or callback.data is None or callback.from_user is None:
        return
    _, _, admin_id, participant_id = callback.data.split(":")
    if callback.from_user.id != int(participant_id):
        await callback.answer("Это сообщение адресовано другому участнику", show_alert=True)
        return
    await callback.answer()
    await state.set_state(ParticipantRelay.reply)
    await state.update_data(relay_admin_id=int(admin_id))
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        ru.relay_reply_prompt_text(),
        reply_markup=cancel_button("relay:cancel"),
        new_message=True,
    )


@router.callback_query(F.data == "relay:cancel")
async def on_cancel(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    if callback.message is not None:
        await notifications.send_text(
            bot, callback.message.chat.id, ru.relay_cancelled_text(), new_message=True
        )


@router.message(ParticipantRelay.reply, Command("cancel"))
async def on_cancel_command(message: Message, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    await notifications.send_text(bot, message.chat.id, ru.relay_cancelled_text())


@router.message(ParticipantRelay.reply, F.text, ~F.text.startswith("/"))
async def on_reply_text(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    admin_id = int(data.get("relay_admin_id", 0))
    if not admin_id:
        await state.clear()
        return
    text = (message.text or "").strip()
    if not text:
        await notifications.send_text(bot, message.chat.id, ru.relay_empty_text())
        return
    if len(text) > ru.RELAY_MESSAGE_LIMIT:
        await notifications.send_text(
            bot, message.chat.id, ru.relay_too_long_text(ru.RELAY_MESSAGE_LIMIT)
        )
        return
    result = await notifications.send_text(
        bot,
        admin_id,
        ru.relay_to_admin_text(user_id=user.id, name=display_name(user), text=text),
        reply_markup=admin_reply_button(user.id),
    )
    await state.clear()
    if not result.ok:
        reason = result.error or "неизвестная ошибка"
        await notifications.send_text(bot, message.chat.id, ru.relay_failed_text(reason))
        return
    await audit_repo.log(
        session,
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        action="message_received",
        payload={"channel": "bot", "to_admin": admin_id},
    )
    await notifications.send_text(bot, message.chat.id, ru.relay_sent_text())
