"""Russian UI texts and formatting helpers.

All user-facing copy lives here. Dynamic values are escaped for MarkdownV2.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo

from bot.domain.money import format_money
from bot.domain.tickets import compress_ranges
from bot.texts.markdown import escape_markdown_v2 as esc
from bot.utils.time import format_datetime


def plural_ru(number: int, one: str, few: str, many: str) -> str:
    """Russian pluralization: 1 билет, 2 билета, 5 билетов."""
    absolute = abs(number) % 100
    if 11 <= absolute <= 19:
        return many
    last = absolute % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def tickets_label(number: int) -> str:
    return f"{number} {plural_ru(number, 'билет', 'билета', 'билетов')}"


def numbers_label(number: int) -> str:
    return f"{number} {plural_ru(number, 'номерок', 'номерка', 'номерков')}"


def applications_label(number: int) -> str:
    return f"{number} {plural_ru(number, 'заявка', 'заявки', 'заявок')}"


class HasRaffle(Protocol):
    title: str
    description: str
    ticket_price: Decimal
    min_amount: Decimal
    prizes_count: int
    end_at: datetime
    channel_title: str
    channel_url: str


def channel_line(channel_title: str | None) -> str:
    if not channel_title:
        return ""
    return f"«{esc(channel_title)}»"


def consent_text(
    *,
    rules_url: str,
    privacy_url: str,
    age_required: bool,
    rules_version: str,
    privacy_version: str,
) -> str:
    text = (
        "Чтобы участвовать, примите правила розыгрыша и политику конфиденциальности\\.\n\n"
        f"[Правила розыгрыша]({esc(rules_url)})\n"
        f"[Политика конфиденциальности]({esc(privacy_url)})\n\n"
        f"Версия правил: {esc(rules_version)}\\. Версия политики: {esc(privacy_version)}\\."
    )
    if age_required:
        text += "\n\nПодтвердите, что вам 18 лет или больше\\."
    return text


def no_active_raffle_text(channel_title: str | None) -> str:
    title = channel_line(channel_title)
    if title:
        return f"Сейчас нет активного розыгрыша\\.\n\nСледующий анонс появится на канале {title}\\."
    return "Сейчас нет активного розыгрыша\\."


def scheduled_raffle_text(raffle: HasRaffle, tz: ZoneInfo) -> str:
    return (
        f"🎗 *{esc(raffle.title)}*\n\n"
        f"Розыгрыш начнётся {esc(format_datetime(raffle.start_at, tz))}\\.\n"
        "Загляните позже\\."
    )


def active_raffle_text(raffle: HasRaffle, tz: ZoneInfo, *, has_consent: bool) -> str:
    base = (
        f"🎗 *{esc(raffle.title)}*\n\n"
        f"{esc(raffle.description)}\n\n"
        f"💰 Цена билета: *{esc(format_money(raffle.ticket_price))} BYN*\n"
        f"🏆 Призов: *{raffle.prizes_count}*\n"
        f"📅 Приём заявок: до {esc(format_datetime(raffle.end_at, tz))}\n"
        f"🎟 За каждые {esc(format_money(raffle.ticket_price))} BYN — 1 билет\\.\n"
        "Билеты считаются по общей сумме всех ваших подтверждённых пожертвований "
        "в этом розыгрыше\\.\n\n"
        "Выберите организацию, которой хотите помочь:"
    )
    if not has_consent:
        base = (
            "Для участия нужно принять правила розыгрыша и политику "
            "конфиденциальности\\.\n\n" + base
        )
    return base


def payment_methods_text(organization_name: str) -> str:
    return f"🏢 *{esc(organization_name)}*\n\nВыберите способ оплаты:"


def payment_instructions_text(
    *,
    organization_name: str,
    payment_method_title: str,
    payment_instructions: str,
    payment_code: str,
    min_amount: Decimal,
    ticket_price: Decimal,
) -> str:
    return (
        f"🏢 *{esc(organization_name)}*\n"
        f"💳 *{esc(payment_method_title)}*\n\n"
        f"{payment_instructions}\n\n"
        f"🔖 Код платежа: `{esc(payment_code)}`\n"
        "Если банк позволяет указать назначение платежа, добавьте этот код — "
        "организации будет проще найти ваш перевод\\.\n\n"
        "После перевода введите сумму в BYN, которую вы отправили на счёт организации\\.\n"
        f"Минимальная сумма для участия — *{esc(format_money(min_amount))} BYN*\\.\n"
        f"За каждые *{esc(format_money(ticket_price))} BYN* — 1 билет\\.\n\n"
        "Перевод отправляется напрямую организации\\. Бот не принимает и не хранит "
        "денежные средства\\."
    )


def invalid_amount_text(reason: str, *, min_amount: Decimal, max_amount: Decimal) -> str:
    if reason == "below_min":
        return (
            f"❌ Минимальная сумма для участия — {esc(format_money(min_amount))} BYN\\. "
            "Пожалуйста, введите корректную сумму\\."
        )
    if reason == "above_max":
        return (
            f"❌ Максимальная сумма одной заявки — {esc(format_money(max_amount))} BYN\\. "
            "Пожалуйста, введите сумму не больше этой\\."
        )
    if reason == "too_many_decimals":
        return (
            "❌ Сумма может содержать не больше двух знаков после запятой\\. "
            "Например: 9 или 9,50\\."
        )
    if reason in {"non_positive", "not_a_number", "not_finite"}:
        return "❌ Не удалось распознать сумму\\. Введите число в BYN, например: 9 или 9,50\\."
    return (
        f"❌ Минимальная сумма для участия — {esc(format_money(min_amount))} BYN\\. "
        "Пожалуйста, введите корректную сумму\\."
    )


def amount_preview_text(
    *,
    amount: Decimal,
    current_amount: Decimal,
    preview_amount: Decimal,
    preview_tickets: int,
    preview_delta: int,
) -> str:
    return (
        f"{esc(format_money(amount))} BYN\\. С учётом уже подтверждённых "
        f"{esc(format_money(current_amount))} BYN всего будет "
        f"{esc(format_money(preview_amount))} BYN = {tickets_label(preview_tickets)}\\.\n"
        f"После проверки чека вы получите ещё {numbers_label(preview_delta)}\\.\n\n"
        "📎 Пришлите чек одним файлом:\n"
        "• фотографией (как фото в Telegram);\n"
        "• PDF\\-документом;\n"
        "• изображением JPEG/PNG, отправленным документом\\.\n\n"
        "На чеке должны быть видны получатель, сумма, дата и статус операции\\."
    )


def invalid_file_text() -> str:
    return (
        "❌ Принимаются фото, PDF или изображение (JPEG/PNG) документом\\. "
        "Текст, стикеры и другие документы не подходят\\. "
        "Пришлите, пожалуйста, чек подходящим файлом\\."
    )


def file_too_large_text(max_mb: int) -> str:
    return f"❌ Файл слишком большой\\. Максимальный размер — {max_mb} МБ\\."


def pending_review_text(
    *,
    public_id: str,
    organization_name: str,
    payment_method_title: str,
    amount: Decimal,
) -> str:
    return (
        f"✅ Заявка №{esc(public_id)} отправлена на проверку\\.\n\n"
        f"Организация: {esc(organization_name)}\n"
        f"Способ оплаты: {esc(payment_method_title)}\n"
        f"Сумма: {esc(format_money(amount))} BYN\n\n"
        "Билеты рассчитываются по общей сумме всех подтверждённых пожертвований "
        "в этом розыгрыше\\. Номера присваиваются после подтверждения оплаты\\."
    )


def admin_card_text(
    *,
    public_id: str,
    payment_code: str,
    user_mention: str,
    organization_name: str,
    payment_method_title: str,
    amount: Decimal,
    current_amount: Decimal,
    preview_delta: int,
    preview_tickets: int,
    submitted_at: datetime | None,
    tz: ZoneInfo,
    raffle_title: str,
) -> str:
    return (
        "📥 *Новая заявка на розыгрыш*\n\n"
        f"🆔 Заявка: `{esc(public_id)}`\n"
        f"🔖 Код платежа: `{esc(payment_code)}`\n"
        f"👤 Участник: {user_mention}\n"
        f"🏢 Организация: *{esc(organization_name)}*\n"
        f"💳 Способ оплаты: {esc(payment_method_title)}\n"
        f"💰 Сумма: *{esc(format_money(amount))} BYN*\n"
        f"📊 Уже подтверждено у участника: {esc(format_money(current_amount))} BYN\n"
        f"🎫 Предварительно: \\+{tickets_label(preview_delta)} "
        f"\\(всего {tickets_label(preview_tickets)}\\)\n"
        f"📅 Дата: {esc(format_datetime(submitted_at, tz))}\n"
        f"🎗 Розыгрыш: {esc(raffle_title)}"
    )


def confirmed_card_text(
    *,
    public_id: str,
    reviewer: str,
    confirmed_at: datetime,
    tz: ZoneInfo,
    ranges: str,
    total_tickets: int,
) -> str:
    return (
        f"✅ *Заявка {esc(public_id)} подтверждена*\n\n"
        f"Выданы номера: *{esc(ranges)}*\n"
        f"Всего билетов у участника: {total_tickets}\n"
        f"Проверил: {esc(reviewer)}\n"
        f"Время: {esc(format_datetime(confirmed_at, tz))}"
    )


def rejected_card_text(
    *, public_id: str, reason: str, reviewer: str, rejected_at: datetime, tz: ZoneInfo
) -> str:
    return (
        f"❌ *Заявка {esc(public_id)} отклонена*\n\n"
        f"Причина: {esc(reason)}\n"
        f"Проверил: {esc(reviewer)}\n"
        f"Время: {esc(format_datetime(rejected_at, tz))}"
    )


def user_confirmation_text(
    *,
    public_id: str,
    amount: Decimal,
    organization_name: str,
    total_amount: Decimal,
    new_numbers: str,
    total_tickets: int,
    all_numbers: str,
    channel_title: str | None,
    channel_url: str | None,
) -> str:
    text = (
        "✅ *Оплата подтверждена!*\n\n"
        f"Заявка №{esc(public_id)}\\.\n"
        f"Вы пожертвовали *{esc(format_money(amount))} BYN* в "
        f"*{esc(organization_name)}*\\.\n\n"
        f"Всего подтверждено: *{esc(format_money(total_amount))} BYN*\\.\n"
        f"Новые номерки: *{esc(new_numbers)}*\\.\n"
        f"Всего у вас {tickets_label(total_tickets)}: *{esc(all_numbers)}*\\.\n\n"
        "Желаем удачи!"
    )
    if channel_title and channel_url:
        text += (
            "\n⚠️ Пожалуйста, не забудьте подписаться на канал "
            f"[«{esc(channel_title)}»]({esc(channel_url)}) — именно там мы объявим "
            "результаты розыгрыша!"
        )
    return text


def rejection_text(*, public_id: str, reason: str, support_contact: str | None) -> str:
    text = (
        f"❌ К сожалению, не удалось подтвердить оплату по заявке №{esc(public_id)}\\.\n\n"
        f"Причина: {esc(reason)}"
    )
    if support_contact:
        text += f"\n\nЕсли вы считаете, что это ошибка, напишите {esc(support_contact)}\\."
    return text


def revision_request_text(public_id: str) -> str:
    return (
        "📎 Чек не удалось прочитать\\. Пришлите, пожалуйста, более чёткое фото, PDF "
        f"или изображение документом по заявке №{esc(public_id)}\\."
    )


def closed_text(channel_title: str | None) -> str:
    if channel_title:
        return (
            "⛔ Приём заявок завершён! Розыгрыш закрыт, ожидайте результатов "
            f"на канале «{esc(channel_title)}»\\."
        )
    return "⛔ Приём заявок завершён! Розыгрыш закрыт, ожидайте результатов\\."


def no_access_text() -> str:
    return "У вас нет доступа к админ-панели\\."


def use_dm_text() -> str:
    return "Админ\\-панель работает в личном чате с ботом\\. Напишите мне в личные сообщения\\."


def my_summary_text(
    *,
    raffle_title: str,
    confirmed_amount: Decimal,
    tickets: int,
    ranges: str,
) -> str:
    numbers = ranges or "пока нет"
    return (
        f"🎗 *{esc(raffle_title)}*\n\n"
        f"Подтверждённая сумма: *{esc(format_money(confirmed_amount))} BYN*\n"
        f"Билеты: *{tickets_label(tickets)}*\n"
        f"Ваши номера: {esc(numbers)}"
    )


def ranges_display(ranges: list[tuple[int, int]]) -> str:
    return compress_ranges(ranges) or "—"


def application_line(
    *,
    public_id: str,
    organization: str,
    method: str | None,
    amount: Decimal | None,
    status: str,
    created_at: datetime,
    tz: ZoneInfo,
) -> str:
    amount_text = f"{esc(format_money(amount))} BYN" if amount is not None else "—"
    method_text = esc(method) if method else "—"
    return (
        f"`{esc(public_id)}` · {esc(organization)} · {method_text}\n"
        f"💰 {amount_text} · {esc(status)}\n"
        f"📅 {esc(format_datetime(created_at, tz))}"
    )


STATUS_LABELS: dict[str, str] = {
    "draft": "черновик",
    "awaiting_receipt": "ожидает чек",
    "pending_review": "на проверке",
    "revision_requested": "нужен новый чек",
    "confirmed": "подтверждена",
    "rejected": "отклонена",
    "cancelled": "отменена",
    "expired": "истекла",
    "voided": "аннулирована",
}

RAFFLE_STATUS_LABELS: dict[str, str] = {
    "draft": "черновик",
    "scheduled": "запланирован",
    "active": "активен",
    "closed": "приём закрыт",
    "finished": "завершён",
    "archived": "в архиве",
}

REJECTION_REASONS: dict[str, str] = {
    "unreadable": "чек не читается",
    "not_found": "платёж не найден",
    "amount_mismatch": "сумма не совпадает",
    "wrong_organization": "перевод в другую организацию",
    "duplicate": "чек уже был использован",
    "other": "другое",
}
