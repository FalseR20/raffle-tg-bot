"""Shared helpers for admin panel handlers."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import RaffleStatus
from bot.db.models import Raffle
from bot.db.repositories import raffles as raffles_repo
from bot.services import notifications
from bot.services.permissions import Permissions
from bot.texts import ru
from bot.texts.markdown import escape_markdown_v2
from bot.utils.time import datetime_input_example, to_timezone, utcnow


def is_private(event: TelegramObject) -> bool:
    if isinstance(event, Message):
        return event.chat.type == "private"
    if isinstance(event, CallbackQuery) and event.message is not None:
        return event.message.chat.type == "private"
    return True


async def ensure_private(event: TelegramObject, bot: Bot) -> bool:
    if is_private(event):
        return True
    chat_id = getattr(event, "chat", None)
    if isinstance(event, Message):
        chat_id = event.chat.id
    elif isinstance(event, CallbackQuery) and event.message:
        chat_id = event.message.chat.id
    if chat_id:
        await notifications.send_text(bot, chat_id, ru.use_dm_text())
    return False


async def deny(event: TelegramObject, bot: Bot, text: str | None = None) -> None:
    message = text or ru.no_access_text()
    if isinstance(event, CallbackQuery):
        await event.answer(message, show_alert=True)
    else:
        await notifications.send_text(bot, event.chat.id, message)


def parse_decimal(raw: str) -> Decimal:
    normalized = raw.strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        value = Decimal(normalized)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Введите число, например 5 или 5,50.") from exc
    if not value.is_finite():
        raise ValueError("Введите конечное число.")
    if value.as_tuple().exponent < -2:
        raise ValueError("Не больше двух знаков после запятой.")
    return value.quantize(Decimal("0.01"))


def parse_datetime(raw: str, tz: ZoneInfo, *, now: datetime | None = None) -> datetime:
    cleaned = raw.strip()
    formats = ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d")
    for fmt in formats:
        try:
            parsed = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        if fmt in {"%d.%m.%Y", "%Y-%m-%d"}:
            parsed = parsed.replace(hour=23, minute=59)
        return parsed.replace(tzinfo=tz)
    example = datetime_input_example(now or utcnow(), tz)
    raise ValueError(f"Введите дату в формате ДД.ММ.ГГГГ ЧЧ:ММ, например {example}.")


def format_dt(value: datetime, tz: ZoneInfo) -> str:
    return to_timezone(value, tz).strftime("%d.%m.%Y %H:%M")


async def load_raffle(
    session: AsyncSession, event: CallbackQuery | Message, callback_prefix: str
) -> Raffle | None:
    if isinstance(event, CallbackQuery):
        if event.data is None:
            return None
        raffle_id = int(event.data.rsplit(":", 1)[1])
    else:
        return None
    raffle = await raffles_repo.get(session, raffle_id)
    if raffle is None and isinstance(event, CallbackQuery):
        await event.answer("Розыгрыш не найден", show_alert=True)
    return raffle


async def send_admin_menu(
    bot: Bot,
    chat_id: int,
    session: AsyncSession,
    permissions: Permissions,
    name: str,
) -> None:
    from bot.keyboards.admin import main_menu

    quick_raffles: list[tuple[int, str, RaffleStatus]] = []
    if permissions.can_manage_raffles:
        raffles = await raffles_repo.list_non_archived(session, limit=20)
        quick_raffles = [(raffle.id, raffle.title, raffle.status) for raffle in raffles]
    role = (
        "администратор"
        if permissions.is_admin
        else ("организатор" if permissions.is_organizer else "представитель организации")
    )
    text = (
        f"⚙️ *Админ\\-панель*\n\n{escape_markdown_v2(name)} · {escape_markdown_v2(role)}\n\n"
        "Выберите раздел или откройте розыгрыш:"
    )
    await notifications.send_text(
        bot, chat_id, text, reply_markup=main_menu(permissions, quick_raffles)
    )


def can_manage(permissions: Permissions) -> bool:
    return permissions.can_manage_raffles


def no_access_text() -> str:
    return ru.no_access_text()
