"""Admin panel entry point and menu."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.db.models import User
from bot.handlers.admin.common import ensure_private, send_admin_menu
from bot.services import notifications
from bot.services.permissions import Permissions
from bot.services.settings import RuntimeSettings
from bot.texts import ru
from bot.texts.markdown import escape_markdown_v2

router = Router(name="admin_menu")


@router.message(Command("admin"))
async def cmd_admin(
    message: Message,
    bot: Bot,
    user: User,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
) -> None:
    if not permissions.has_admin_panel:
        await notifications.send_text(bot, message.chat.id, ru.no_access_text())
        return
    if not await ensure_private(message, bot):
        return
    welcome = ""
    if runtime_settings.greeting_text:
        welcome = escape_markdown_v2(runtime_settings.greeting_text) + "\n\n"
    await send_admin_menu(bot, message.chat.id, permissions, user.first_name or "—")
    if welcome:
        await notifications.send_text(bot, message.chat.id, welcome)


@router.callback_query(F.data == "adm:menu")
async def on_menu(
    callback: CallbackQuery,
    bot: Bot,
    user: User,
    permissions: Permissions,
) -> None:
    if callback.message is None:
        return
    if not permissions.has_admin_panel:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if callback.message.chat.type != "private":
        await callback.answer("Админ-панель доступна только в личном чате", show_alert=True)
        return
    await callback.answer()
    await send_admin_menu(bot, callback.message.chat.id, permissions, user.first_name or "—")


@router.callback_query(F.data == "adm:help")
async def on_help(callback: CallbackQuery, bot: Bot, permissions: Permissions) -> None:
    if callback.message is None:
        return
    if not permissions.is_org_rep or permissions.can_manage_raffles:
        await callback.answer()
        await notifications.send_text(
            bot,
            callback.message.chat.id,
            "Раздел помощи админ\\-панели\\. Используйте меню или /admin\\.",
        )
        return
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "Вы видите заявки только своей организации\\. "
        "Подтверждение и отклонение выполняются в чате проверки или здесь, в личном чате\\.",
    )
