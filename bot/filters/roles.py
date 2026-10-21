"""Role-based aiogram filters. Permissions are still rechecked in services."""

from __future__ import annotations

from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.services.permissions import Permissions


def _permissions(event: TelegramObject, data: dict[str, Any]) -> Permissions | None:
    return data.get("permissions")


class HasAdminPanel(BaseFilter):
    async def __call__(self, event: TelegramObject, permissions: Permissions | None = None) -> bool:
        return bool(permissions and permissions.has_admin_panel)


class ManagersOnly(BaseFilter):
    async def __call__(self, event: TelegramObject, permissions: Permissions | None = None) -> bool:
        return bool(permissions and permissions.can_manage_raffles)


class AdminsOnly(BaseFilter):
    async def __call__(self, event: TelegramObject, permissions: Permissions | None = None) -> bool:
        return bool(permissions and permissions.can_manage_organizers)


class PrivateChatOnly(BaseFilter):
    async def __call__(self, event: TelegramObject) -> bool:
        if isinstance(event, Message):
            return event.chat.type == "private"
        if isinstance(event, CallbackQuery) and event.message is not None:
            return event.message.chat.type == "private"
        return True
