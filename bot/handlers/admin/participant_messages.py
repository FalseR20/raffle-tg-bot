"""Admin → participant messages relayed through the bot.

Some participants have no public username or restrict ``tg://user?id`` links
(``BUTTON_USER_PRIVACY_RESTRICTED``), so the bot is the only delivery channel.
The participant gets a reply button that routes the answer back to the admin
who started the conversation.
"""

from __future__ import annotations

from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import users as users_repo
from bot.keyboards.admin import participant_card
from bot.keyboards.relay import cancel_button, participant_reply_button
from bot.services import notifications
from bot.services.permissions import Permissions
from bot.services.users import display_name
from bot.states import AdminRelay
from bot.texts import ru

router = Router(name="admin_messages")


@router.callback_query(F.data.regexp(r"^adm:talk:\d+$"))
async def on_start_message(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    if callback.message is None or callback.data is None:
        return
    if not permissions.can_manage_raffles:
        await callback.answer("Нет доступа", show_alert=True)
        return
    target_id = int(callback.data.rsplit(":", 1)[1])
    participant = await users_repo.get(session, target_id)
    if participant is None:
        await callback.answer("Участник не найден", show_alert=True)
        return
    await callback.answer()
    await state.set_state(AdminRelay.compose)
    await state.update_data(relay_target_id=target_id)
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        ru.relay_prompt_text(user_id=target_id, name=display_name(participant)),
        reply_markup=cancel_button("adm:talk_cancel"),
    )


@router.callback_query(F.data == "adm:talk_cancel")
async def on_cancel(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    if callback.message is not None:
        await notifications.send_text(
            bot,
            callback.message.chat.id,
            ru.relay_cancelled_text(),
            reply_markup=participant_card(),
        )


@router.message(AdminRelay.compose, Command("cancel"))
async def on_cancel_command(message: Message, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    await notifications.send_text(
        bot, message.chat.id, ru.relay_cancelled_text(), reply_markup=participant_card()
    )


@router.message(AdminRelay.compose, F.text, ~F.text.startswith("/"))
async def on_message_text(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user: User,
    permissions: Permissions,
    state: FSMContext,
    now: datetime,
) -> None:
    if not permissions.can_manage_raffles:
        await state.clear()
        return
    data = await state.get_data()
    target_id = int(data.get("relay_target_id", 0))
    if not target_id:
        await state.clear()
        return
    text = (message.text or "").strip()
    if not text:
        await notifications.send_text(bot, message.chat.id, ru.relay_empty_text(), new_message=True)
        return
    if len(text) > ru.RELAY_MESSAGE_LIMIT:
        await notifications.send_text(
            bot,
            message.chat.id,
            ru.relay_too_long_text(ru.RELAY_MESSAGE_LIMIT),
            new_message=True,
        )
        return
    result = await notifications.send_text(
        bot,
        target_id,
        ru.relay_to_participant_text(text),
        reply_markup=participant_reply_button(admin_id=user.id, participant_id=target_id),
    )
    await state.clear()
    if not result.ok:
        reason = result.error or "неизвестная ошибка"
        await notifications.send_text(
            bot,
            message.chat.id,
            ru.relay_failed_text(reason),
            reply_markup=participant_card(),
        )
        return
    await audit_repo.log(
        session,
        entity_type="user",
        entity_id=target_id,
        actor_id=user.id,
        action="message_sent",
        payload={"channel": "bot"},
        at=now,
    )
    await notifications.send_text(
        bot, message.chat.id, ru.relay_sent_text(), reply_markup=participant_card()
    )
