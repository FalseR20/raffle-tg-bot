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
from bot.texts.markdown import escape_url, safe_markdown
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
    return f"{number} {plural_ru(number, 'номер', 'номера', 'номеров')}"


def applications_label(number: int) -> str:
    return f"{number} {plural_ru(number, 'заявка', 'заявки', 'заявок')}"


def winners_label(number: int) -> str:
    return f"{number} {plural_ru(number, 'победитель', 'победителя', 'победителей')}"


def draft_raffle_delete_prompt(title: str, *, applications: int) -> str:
    """Confirmation before a draft raffle and its receipts are removed."""
    if applications:
        details = (
            f"Вместе с ним будут удалены {esc(applications_label(applications))} "
            "и файлы чеков\\. Организации и журнал аудита сохранятся\\."
        )
    else:
        details = "Заявок в этом черновике нет\\. Журнал аудита сохранится\\."
    return f"Удалить черновик «{esc(title)}»\\?\n\n{details}\n\nЭто действие нельзя отменить\\."


def archived_raffle_delete_prompt(title: str, *, applications: int, winners: int) -> str:
    """Confirmation before an archived raffle and its receipts are removed."""
    return (
        f"Удалить архивный розыгрыш «{esc(title)}»\\?\n\n"
        f"Вместе с ним будут удалены {esc(applications_label(applications))}, "
        f"{esc(winners_label(winners))} и файлы чеков\\. "
        "Организации и журнал аудита сохранятся\\.\n\n"
        "Это действие нельзя отменить\\."
    )


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
        "Для участия примите правила розыгрыша и политику конфиденциальности\\.\n\n"
        f"[Правила розыгрыша]({escape_url(rules_url)})\n"
        f"[Политика конфиденциальности]({escape_url(privacy_url)})\n\n"
        f"Версии: правила {esc(rules_version)} · политика {esc(privacy_version)}\\."
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
        f"📅 Приём заявок до {esc(format_datetime(raffle.end_at, tz))}\n"
        f"🎟 1 билет за каждые *{esc(format_money(raffle.ticket_price))} BYN*\\.\n"
        "Билеты суммируются по всем вашим подтверждённым пожертвованиям в этом розыгрыше\\.\n\n"
        "Выберите организацию:"
    )
    if not has_consent:
        base = "Для участия примите правила розыгрыша и политику конфиденциальности\\.\n\n" + base
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
        f"{safe_markdown(payment_instructions)}\n\n"
        f"🔖 Код платежа: `{esc(payment_code)}`\n"
        "Добавьте код в назначение платежа, если банк позволяет\\.\n\n"
        "После перевода введите отправленную сумму в BYN\\.\n"
        f"Минимум — *{esc(format_money(min_amount))} BYN*\\. "
        f"1 билет за каждые *{esc(format_money(ticket_price))} BYN*\\.\n\n"
        "Перевод идёт напрямую организации\\. Бот не принимает деньги\\."
    )


def invalid_amount_text(reason: str, *, min_amount: Decimal, max_amount: Decimal) -> str:
    if reason == "below_min":
        return (
            f"❌ Минимальная сумма — {esc(format_money(min_amount))} BYN\\. "
            "Введите сумму ещё раз\\."
        )
    if reason == "above_max":
        return (
            f"❌ Максимум для одной заявки — {esc(format_money(max_amount))} BYN\\. "
            "Уменьшите сумму\\."
        )
    if reason == "too_many_decimals":
        return "❌ Не больше двух знаков после запятой\\. Например: 9 или 9,50\\."
    if reason in {"non_positive", "not_a_number", "not_finite"}:
        return "❌ Не удалось распознать сумму\\. Введите число в BYN, например 9 или 9,50\\."
    return f"❌ Минимальная сумма — {esc(format_money(min_amount))} BYN\\. Введите сумму ещё раз\\."


def amount_preview_text(
    *,
    amount: Decimal,
    current_amount: Decimal,
    preview_amount: Decimal,
    preview_tickets: int,
    preview_delta: int,
) -> str:
    return (
        f"Вы указали {esc(format_money(amount))} BYN\\. "
        f"С учётом подтверждённых {esc(format_money(current_amount))} BYN "
        f"итог — {esc(format_money(preview_amount))} BYN \\= {tickets_label(preview_tickets)}\\.\n"
        f"После проверки чека начислим ещё {numbers_label(preview_delta)}\\.\n\n"
        "📎 Пришлите чек одним файлом:\n"
        "• фото в Telegram;\n"
        "• PDF\\-документ;\n"
        "• изображение JPEG/PNG в документе\\.\n\n"
        "На чеке должны быть видны получатель, сумма, дата и статус операции\\."
    )


def invalid_file_text() -> str:
    return (
        "❌ Подойдут фото, PDF\\-документ или изображение JPEG/PNG в документе\\. "
        "Текст, стикеры и другие файлы не принимаются\\."
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
        "Билеты начисляются по общей сумме подтверждённых пожертвований\\. "
        "Номера появятся после проверки оплаты\\."
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
        f"📊 Ранее подтверждено: {esc(format_money(current_amount))} BYN\n"
        f"🎫 Будет начислено: \\+{tickets_label(preview_delta)} "
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
        f"Новые номера: *{esc(ranges)}*\n"
        f"Всего билетов: {total_tickets}\n"
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
        "✅ *Оплата подтверждена\\!*\n\n"
        f"Заявка №{esc(public_id)}\\.\n"
        f"Вы пожертвовали *{esc(format_money(amount))} BYN* в "
        f"*{esc(organization_name)}*\\.\n\n"
        f"Всего подтверждено: *{esc(format_money(total_amount))} BYN*\\.\n"
        f"Новые номера: *{esc(new_numbers)}*\\.\n"
        f"Всего у вас {tickets_label(total_tickets)}: *{esc(all_numbers)}*\\.\n\n"
        "Желаем удачи\\!"
    )
    if channel_title and channel_url:
        text += (
            "\n⚠️ Пожалуйста, не забудьте подписаться на канал "
            f"[«{esc(channel_title)}»]({escape_url(channel_url)}) — именно там мы объявим "
            "результаты розыгрыша\\!"
        )
    return text


def rejection_text(*, public_id: str, reason: str, support_contact: str | None) -> str:
    text = (
        f"❌ Оплату по заявке №{esc(public_id)} подтвердить не удалось\\.\n\nПричина: {esc(reason)}"
    )
    if support_contact:
        text += f"\n\nЕсли вы считаете, что это ошибка, напишите {esc(support_contact)}\\."
    return text


def revision_request_text(public_id: str) -> str:
    return (
        "📎 Чек не читается\\. Пришлите более чёткое фото, PDF или изображение "
        f"документом по заявке №{esc(public_id)}\\."
    )


def closed_text(channel_title: str | None) -> str:
    if channel_title:
        return (
            f"⛔ Приём заявок завершён\\! Ожидайте результатов на канале «{esc(channel_title)}»\\."
        )
    return "⛔ Приём заявок завершён\\! Ожидайте результатов\\."


def no_access_text() -> str:
    return "У вас нет доступа к админ\\-панели\\."


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


def results_text(
    *,
    raffle_title: str,
    confirmed_amount: Decimal,
    tickets: int,
    ranges: str,
    prizes_count: int,
    last_ticket_number: int,
    winners: list[tuple[int, int]],
    user_winning_places: list[tuple[int, int]],
) -> str:
    """Participant-facing results. Shows ticket numbers only, never names."""
    lines = [
        f"🏆 *Итоги: {esc(raffle_title)}*",
        "",
        f"💰 Подтверждено: *{esc(format_money(confirmed_amount))} BYN*",
        f"🎫 Билеты: *{tickets_label(tickets)}*",
        f"🔢 Ваши номера: {esc(ranges or '—')}",
        f"🏅 Призов: {prizes_count} · Билетов: {last_ticket_number}",
        "",
    ]
    if winners:
        lines.append("*Победители:*")
        for place, ticket in winners:
            lines.append(f"{place} место — билет {ticket}")
    else:
        lines.append("Победители ещё не объявлены\\.")
    if user_winning_places:
        lines.append("")
        lines.append("🎉 *Ваш результат:*")
        for place, ticket in user_winning_places:
            lines.append(f"Билет {ticket} занял *{place}* место\\.")
    elif winners and tickets:
        lines.append("")
        lines.append("Ваши номера не вошли в список победителей\\.")
    return "\n".join(lines)
