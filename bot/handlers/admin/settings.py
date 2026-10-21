"""Runtime settings editing."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.keyboards.admin import settings_menu, wizard_cancel
from bot.middlewares.admin import ManagersOnlyMiddleware
from bot.services import notifications
from bot.services.errors import ServiceError
from bot.services.permissions import Permissions
from bot.services.settings import (
    SETTING_KEYS,
    describe_setting,
    load_runtime_settings,
    setting_label,
    update_setting,
)
from bot.states import SettingsWizard
from bot.texts.markdown import escape_markdown_v2
from bot.utils.time import utcnow

router = Router(name="admin_settings")
router.callback_query.middleware(ManagersOnlyMiddleware())
router.message.middleware(ManagersOnlyMiddleware())

EDITABLE_KEYS = [
    "channel_url",
    "channel_title",
    "rules_url",
    "privacy_url",
    "rules_version",
    "privacy_version",
    "support_contact",
    "management_log_chat_id",
    "max_donation",
    "receipt_retention_days",
    "confirm_two_step",
    "enable_random_draw",
    "timezone",
    "require_age_confirmation",
    "greeting_text",
]
assert set(EDITABLE_KEYS) <= set(SETTING_KEYS)


async def _send_settings(bot: Bot, chat_id: int, session: AsyncSession, env: Settings) -> None:
    settings = await load_runtime_settings(session, env)
    entries = []
    for key in EDITABLE_KEYS:
        value = getattr(settings, key)
        if key == "timezone":
            value = settings.timezone_name
        entries.append((key, setting_label(key), describe_setting(key, value)))
    await notifications.send_text(
        bot,
        chat_id,
        "⚙️ *Настройки*\n\nНажмите на настройку, чтобы изменить её\\.",
        reply_markup=settings_menu(entries),
    )


@router.callback_query(F.data == "adm:settings")
async def on_settings(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    env_settings: Settings,
) -> None:
    if callback.message is None:
        return
    if not permissions.can_manage_raffles:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await _send_settings(bot, callback.message.chat.id, session, env_settings)


@router.callback_query(F.data.startswith("adm:setting:"))
async def on_setting(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    env_settings: Settings,
    state: FSMContext,
) -> None:
    if callback.message is None or callback.data is None:
        return
    if not permissions.can_manage_raffles:
        await callback.answer("Нет доступа", show_alert=True)
        return
    key = callback.data.split(":", 2)[2]
    if key not in EDITABLE_KEYS:
        await callback.answer("Настройка недоступна", show_alert=True)
        return
    settings = await load_runtime_settings(session, env_settings)
    current = getattr(settings, key)
    await state.set_state(SettingsWizard.value)
    await state.update_data(setting_key=key)
    await callback.answer()
    hint = "Введите новое значение"
    if isinstance(current, bool):
        hint = "Введите «да» или «нет»"
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        f"{escape_markdown_v2(setting_label(key))}\n\n"
        f"Текущее значение: {escape_markdown_v2(describe_setting(key, current))}\n\n"
        f"{hint}:",
        reply_markup=wizard_cancel("adm:settings"),
    )


@router.message(SettingsWizard.value, F.text)
async def on_setting_value(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    state: FSMContext,
    env_settings: Settings,
) -> None:
    if not permissions.can_manage_raffles:
        return
    data = await state.get_data()
    key = data.get("setting_key")
    if not key:
        await state.clear()
        return
    try:
        await update_setting(
            session,
            key=key,
            raw=message.text or "",
            actor_id=permissions.user_id,
            now=utcnow(),
        )
        await session.commit()
    except ServiceError as exc:
        await notifications.send_text(bot, message.chat.id, escape_markdown_v2(exc.user_message))
        return
    await state.clear()
    await notifications.send_text(bot, message.chat.id, "Сохранено\\.")
    await _send_settings(bot, message.chat.id, session, env_settings)
