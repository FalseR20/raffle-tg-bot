"""Replace the current bot message instead of sending a new one."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.services import notifications


class EditCurrentMessageMiddleware(BaseMiddleware):
    """Turn ``send_text`` calls into edits of the message that started the flow.

    Applies to private-chat callbacks and to replies given while an input state
    is active. Commands and messages outside an active state keep the old
    behaviour and send a new message.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        target = await self._target(event, data)
        if target is None:
            return await handler(event, data)
        with notifications.editing_target(*target):
            return await handler(event, data)

    async def _target(self, event: TelegramObject, data: dict[str, Any]) -> tuple[int, int] | None:
        state = data.get("state")
        if isinstance(event, CallbackQuery):
            message = event.message
            if message is None or message.chat.type != "private":
                return None
            if isinstance(state, FSMContext):
                await state.update_data(ui_message_id=message.message_id)
            return message.chat.id, message.message_id
        if not isinstance(event, Message) or event.chat.type != "private":
            return None
        text = event.text or event.caption or ""
        if text.startswith("/"):
            return None
        if not isinstance(state, FSMContext) or await state.get_state() is None:
            return None
        message_id = (await state.get_data()).get("ui_message_id")
        if not isinstance(message_id, int):
            return None
        return event.chat.id, message_id
