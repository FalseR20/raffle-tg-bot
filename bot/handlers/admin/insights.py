"""Statistics, integrity, audit and winner selection."""

from __future__ import annotations

import csv
import io
import json

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import RaffleStatus
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import raffles as raffles_repo
from bot.db.repositories import winners as winners_repo
from bot.keyboards.admin import audit_actions, draw_menu, winners_menu
from bot.services import notifications
from bot.services.draw import record_manual_winner, reset_winners, run_random_draw
from bot.services.errors import ServiceError
from bot.services.integrity import check_raffle
from bot.services.permissions import Permissions
from bot.services.settings import RuntimeSettings
from bot.services.statistics import build_statistics
from bot.states import WinnerWizard
from bot.texts import ru
from bot.texts.markdown import escape_markdown_v2
from bot.utils.time import format_datetime, utcnow

router = Router(name="admin_insights")


# --------------------------------- statistics --------------------------------


@router.callback_query(F.data == "adm:stats")
async def on_stats(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
) -> None:
    if callback.message is None:
        return
    raffle = await raffles_repo.get_current(session)
    if raffle is None:
        await callback.answer("Нет текущего розыгрыша", show_alert=True)
        return
    await callback.answer()
    await _send_stats(bot, callback.message.chat.id, session, raffle, permissions, runtime_settings)


@router.callback_query(F.data.regexp(r"^adm:stats:\d+$"))
async def on_stats_raffle(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
) -> None:
    if callback.message is None or callback.data is None:
        return
    raffle = await raffles_repo.get(session, int(callback.data.rsplit(":", 1)[1]))
    if raffle is None:
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return
    await callback.answer()
    await _send_stats(bot, callback.message.chat.id, session, raffle, permissions, runtime_settings)


async def _send_stats(
    bot: Bot,
    chat_id: int,
    session: AsyncSession,
    raffle,
    permissions: Permissions,
    settings: RuntimeSettings,
) -> None:
    stats = await build_statistics(
        session, raffle, allowed_organization_ids=permissions.allowed_organization_ids()
    )
    counts = stats.status_counts
    lines = [
        f"📊 *Статистика: {escape_markdown_v2(raffle.title)}*",
        f"Всего заявок: {stats.total_applications}",
        f"На проверке: {counts.get('pending_review', 0) + counts.get('revision_requested', 0)} "
        f"(самая старая: {stats.oldest_pending_days} дн\\.)",
        f"Подтверждено: {counts.get('confirmed', 0)}",
        f"Отклонено: {counts.get('rejected', 0)}",
        f"Отменено: {counts.get('cancelled', 0)}",
        f"Сумма подтверждённых: {escape_markdown_v2(ru.format_money(stats.confirmed_amount))} BYN",
        f"Билеты: {stats.tickets_issued}",
        f"Призов: {stats.prizes_count}",
        f"Участников: {stats.unique_participants}",
        f"Средний донорский взнос: "
        f"{escape_markdown_v2(ru.format_money(stats.average_donation))} BYN",
        f"Старт → чек: {stats.start_to_receipt_percent}%",
        f"Чек → подтверждение: {stats.receipt_to_confirmation_percent}%",
        "",
        "*По организациям:*",
    ]
    for row in stats.by_organization:
        lines.append(
            f"{escape_markdown_v2(row.name)}: {row.applications} / "
            f"{row.confirmed} / {escape_markdown_v2(ru.format_money(row.amount))} BYN"
        )
    lines.append("\n*По способам оплаты:*")
    for row in stats.by_payment_method:
        lines.append(
            f"{escape_markdown_v2(row.name)}: {row.applications} / "
            f"{row.confirmed} / {escape_markdown_v2(ru.format_money(row.amount))} BYN"
        )
    await notifications.send_text(bot, chat_id, "\n".join(lines))


# --------------------------------- integrity ---------------------------------


@router.callback_query(F.data == "adm:integrity")
async def on_integrity(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
) -> None:
    if callback.message is None:
        return
    if not permissions.can_manage_raffles:
        await callback.answer("Нет доступа", show_alert=True)
        return
    raffle = await raffles_repo.get_current(session)
    if raffle is None:
        recent = await raffles_repo.list_all(session, limit=50)
        raffle = next(
            (
                item
                for item in recent
                if item.status in {RaffleStatus.FINISHED, RaffleStatus.ARCHIVED}
            ),
            None,
        )
    if raffle is None:
        await callback.answer("Нет розыгрыша для проверки", show_alert=True)
        return
    report = await check_raffle(session, raffle)
    await callback.answer()
    if report.is_ok:
        await notifications.send_text(
            bot,
            callback.message.chat.id,
            "🛡 *Проверка целостности*\n\n"
            "✅ Расхождений не найдено\\.\n\n"
            f"Счётчик: {report.last_ticket_number}\n"
            f"Сумма по заявкам: {report.confirmed_tickets_sum}\n"
            f"Сумма по участникам: {report.participant_tickets_sum}",
        )
        return
    lines = ["🛡 *Проверка целостности*", "", "⚠️ Обнаружены расхождения:", ""]
    for issue in report.issues[:20]:
        lines.append(f"• {escape_markdown_v2(issue.code)}: {escape_markdown_v2(issue.message)}")
        if issue.details:
            lines.append(
                "  " + escape_markdown_v2(json.dumps(issue.details, ensure_ascii=False)[:300])
            )
    lines.append("\nОстановите подтверждения и проверьте данные вручную\\.")
    await notifications.send_text(bot, callback.message.chat.id, "\n".join(lines))


# ----------------------------------- audit -----------------------------------


@router.callback_query(F.data == "adm:audit")
async def on_audit(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
) -> None:
    if callback.message is None:
        return
    if not permissions.can_manage_raffles:
        await callback.answer("Нет доступа", show_alert=True)
        return
    entries = await audit_repo.list_entries(session, limit=20)
    lines = ["🧾 *Аудит*", ""]
    for entry in entries:
        lines.append(
            f"{escape_markdown_v2(format_datetime(entry.created_at, runtime_settings.timezone))} · "
            f"{escape_markdown_v2(entry.action)} · {entry.actor_id or '—'}"
        )
        if entry.entity_type:
            lines.append(
                f"  {escape_markdown_v2(entry.entity_type)} "
                f"{escape_markdown_v2(entry.entity_id or '')}"
            )
    if not entries:
        lines.append("Записей пока нет\\.")
    await callback.answer()
    await notifications.send_text(
        bot, callback.message.chat.id, "\n".join(lines), reply_markup=audit_actions()
    )


@router.callback_query(F.data == "adm:audit_csv")
async def on_audit_csv(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
) -> None:
    if callback.message is None:
        return
    if not permissions.can_manage_raffles:
        await callback.answer("Нет доступа", show_alert=True)
        return
    entries = await audit_repo.list_entries(session, limit=10000)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        [
            "created_at",
            "actor_id",
            "action",
            "entity_type",
            "entity_id",
            "application_id",
            "raffle_id",
            "old_status",
            "new_status",
            "payload",
        ]
    )
    for entry in entries:
        writer.writerow(
            [
                entry.created_at.isoformat(),
                entry.actor_id or "",
                entry.action,
                entry.entity_type,
                entry.entity_id or "",
                entry.application_id or "",
                entry.raffle_id or "",
                entry.old_status or "",
                entry.new_status or "",
                json.dumps(entry.payload, ensure_ascii=False) if entry.payload else "",
            ]
        )
    data = ("\ufeff" + buffer.getvalue()).encode("utf-8")
    await callback.answer()
    await bot.send_document(
        callback.message.chat.id,
        BufferedInputFile(data, filename="audit_log.csv"),
        caption="🧾 Аудит\\. Файл содержит служебные данные\\.",
        parse_mode="MarkdownV2",
    )
    await audit_repo.log(
        session,
        entity_type="export",
        entity_id="audit_log.csv",
        actor_id=permissions.user_id,
        action="export_created",
        payload={"format": "csv", "kind": "audit"},
        at=utcnow(),
    )
    await session.commit()


# ----------------------------------- draw ------------------------------------


@router.callback_query(F.data == "adm:draw")
async def on_draw(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
) -> None:
    if callback.message is None:
        return
    if not permissions.can_manage_raffles:
        await callback.answer("Нет доступа", show_alert=True)
        return
    raffles = [
        raffle
        for raffle in await raffles_repo.list_all(session, limit=50)
        if raffle.status in {RaffleStatus.FINISHED, RaffleStatus.ARCHIVED}
    ]
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "🎲 *Розыгрыш победителей*\n\n"
        "Запись победителей доступна после завершения розыгрыша\\. "
        "Бумажный розыгрыш остаётся официальным; бот лишь фиксирует результаты\\.",
        reply_markup=draw_menu(
            [(raffle.id, raffle.title) for raffle in raffles],
            random_enabled=runtime_settings.enable_random_draw,
        ),
    )


@router.callback_query(F.data.regexp(r"^adm:winners:\d+$"))
async def on_winners(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
) -> None:
    if callback.message is None or callback.data is None:
        return
    if not permissions.can_manage_raffles:
        await callback.answer("Нет доступа", show_alert=True)
        return
    raffle = await raffles_repo.get(session, int(callback.data.rsplit(":", 1)[1]))
    if raffle is None:
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return
    await _send_winners(bot, callback.message.chat.id, session, raffle)


async def _send_winners(bot: Bot, chat_id: int, session: AsyncSession, raffle) -> None:
    winners = await winners_repo.list_for_raffle(session, raffle.id)
    lines = [f"🏆 *Победители: {escape_markdown_v2(raffle.title)}*", ""]
    if winners:
        for winner in winners:
            application = winner.application
            lines.append(
                f"{winner.place} место · билет {winner.ticket_number} · "
                f"ID {application.user_id} · заявка `{escape_markdown_v2(application.public_id)}`"
            )
    else:
        lines.append("Победители ещё не записаны\\.")
    await notifications.send_text(
        bot, chat_id, "\n".join(lines), reply_markup=winners_menu(raffle.id)
    )


@router.callback_query(F.data.regexp(r"^adm:winner_add:\d+$"))
async def on_winner_add(
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
    raffle = await raffles_repo.get(session, int(callback.data.rsplit(":", 1)[1]))
    if raffle is None:
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return
    await state.set_state(WinnerWizard.place)
    await state.update_data(raffle_id=raffle.id)
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        f"Введите место от 1 до {raffle.prizes_count}:",
    )


@router.message(WinnerWizard.place, F.text)
async def on_winner_place(
    message: Message, bot: Bot, state: FSMContext, session: AsyncSession, permissions: Permissions
) -> None:
    try:
        place = int((message.text or "").strip())
    except ValueError:
        await notifications.send_text(bot, message.chat.id, "Введите целое число\\.")
        return
    data = await state.get_data()
    raffle = await raffles_repo.get(session, int(data.get("raffle_id", 0)))
    if raffle is None:
        await state.clear()
        return
    if not 1 <= place <= raffle.prizes_count:
        await notifications.send_text(
            bot, message.chat.id, f"Место должно быть от 1 до {raffle.prizes_count}\\."
        )
        return
    await state.update_data(place=place)
    await state.set_state(WinnerWizard.ticket)
    await notifications.send_text(
        bot,
        message.chat.id,
        f"Введите номер билета от 1 до {raffle.last_ticket_number}:",
    )


@router.message(WinnerWizard.ticket, F.text)
async def on_winner_ticket(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    try:
        ticket = int((message.text or "").strip())
    except ValueError:
        await notifications.send_text(bot, message.chat.id, "Введите целое число\\.")
        return
    data = await state.get_data()
    raffle = await raffles_repo.get(session, int(data.get("raffle_id", 0)))
    if raffle is None:
        await state.clear()
        return
    try:
        await record_manual_winner(
            session,
            actor=permissions,
            raffle=raffle,
            place=int(data["place"]),
            ticket_number=ticket,
            now=utcnow(),
        )
        await session.commit()
    except ServiceError as exc:
        await notifications.send_text(bot, message.chat.id, escape_markdown_v2(exc.user_message))
        return
    await state.clear()
    await notifications.send_text(bot, message.chat.id, "✅ Победитель записан\\.")
    await _send_winners(bot, message.chat.id, session, raffle)


@router.callback_query(F.data.regexp(r"^adm:winners_reset:\d+$"))
async def on_winners_reset(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
) -> None:
    if callback.message is None or callback.data is None:
        return
    raffle = await raffles_repo.get(session, int(callback.data.rsplit(":", 1)[1]))
    if raffle is None:
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return
    try:
        await reset_winners(session, actor=permissions, raffle=raffle, now=utcnow())
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer("Победители сброшены")
    await _send_winners(bot, callback.message.chat.id, session, raffle)


@router.callback_query(F.data == "adm:draw_random")
async def on_random_prompt(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
) -> None:
    if callback.message is None:
        return
    if not permissions.can_manage_raffles:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if not runtime_settings.enable_random_draw:
        await callback.answer("Случайный розыгрыш выключен в настройках", show_alert=True)
        return
    raffles = [
        raffle
        for raffle in await raffles_repo.list_all(session, limit=50)
        if raffle.status is RaffleStatus.FINISHED
    ]
    rows = [
        [InlineKeyboardButton(text=raf.title[:50], callback_data=f"adm:draw_random_do:{raf.id}")]
        for raf in raffles
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:draw")])
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "Выберите завершённый розыгрыш:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.regexp(r"^adm:draw_random_do:\d+$"))
async def on_random_draw(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
) -> None:
    if callback.message is None or callback.data is None:
        return
    if not runtime_settings.enable_random_draw:
        await callback.answer("Случайный розыгрыш выключен", show_alert=True)
        return
    raffle = await raffles_repo.get(session, int(callback.data.rsplit(":", 1)[1]))
    if raffle is None:
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return
    try:
        await run_random_draw(session, actor=permissions, raffle=raffle, now=utcnow())
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer("Розыгрыш проведён")
    await _send_winners(bot, callback.message.chat.id, session, raffle)
