"""Guard for admin-panel callbacks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject

from bot.services.permissions import Permissions


class AdminPanelMiddleware(BaseMiddleware):
    """Every admin callback requires a panel role and a private chat."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        permissions: Permissions | None = data.get("permissions")
        if permissions is None or not permissions.has_admin_panel:
            if isinstance(event, CallbackQuery):
                await event.answer("У вас нет доступа к админ-панели", show_alert=True)
            return None
        if (
            isinstance(event, CallbackQuery)
            and event.message is not None
            and event.message.chat.type != "private"
        ):
            await event.answer("Админ-панель работает в личном чате с ботом", show_alert=True)
            return None
        return await handler(event, data)


class ManagersOnlyMiddleware(BaseMiddleware):
    """Guard manager-only admin sections (raffles, organizations, roles, settings)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        permissions: Permissions | None = data.get("permissions")
        if permissions is None or not permissions.can_manage_raffles:
            if isinstance(event, CallbackQuery):
                await event.answer("Нет доступа", show_alert=True)
            return None
        return await handler(event, data)
