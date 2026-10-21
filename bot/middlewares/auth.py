"""User, permissions and runtime settings middleware."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.services import users as users_service
from bot.services.permissions import load_permissions
from bot.services.settings import load_runtime_settings
from bot.utils.time import utcnow


class AuthMiddleware(BaseMiddleware):
    def __init__(self, env: Settings) -> None:
        self.env = env

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        actual = event
        if isinstance(actual, Update):
            actual = actual.message or actual.callback_query or actual
        tg_user: TgUser | None = getattr(actual, "from_user", None)
        session: AsyncSession | None = data.get("session")
        if tg_user is None or tg_user.is_bot or session is None:
            return await handler(event, data)

        now = utcnow()
        user = await users_service.touch_user(
            session,
            users_service.TelegramProfile(
                user_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                language_code=tg_user.language_code,
            ),
            now=now,
        )
        permissions = await load_permissions(
            session, tg_user.id, env_admin_ids=self.env.admin_id_set
        )
        runtime_settings = await load_runtime_settings(session, self.env)
        data["user"] = user
        data["permissions"] = permissions
        data["runtime_settings"] = runtime_settings
        data["env_settings"] = self.env
        data["now"] = now
        return await handler(event, data)


def is_private_chat(event: TelegramObject) -> bool:
    if isinstance(event, Message):
        return event.chat.type == "private"
    if isinstance(event, CallbackQuery) and event.message is not None:
        return event.message.chat.type == "private"
    return True
