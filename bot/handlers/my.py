"""Participant's own applications."""

from __future__ import annotations

from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ApplicationStatus, RaffleStatus
from bot.db.models import User
from bot.db.repositories import applications as apps_repo
from bot.domain.money import format_money
from bot.handlers.views import current_raffle, send_no_raffle
from bot.keyboards.participant import cancel_confirmation, my_applications_keyboard
from bot.services import applications as applications_service
from bot.services import notifications
from bot.services.errors import ServiceError
from bot.services.settings import RuntimeSettings
from bot.services.users import has_valid_consent
from bot.states import ParticipantFlow
from bot.texts import ru
from bot.texts.markdown import escape_markdown_v2

router = Router(name="my")


@router.message(Command("my"))
async def cmd_my(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user: User,
    runtime_settings: RuntimeSettings,
    now: datetime,
) -> None:
    raffle = await current_raffle(session, now)
    if raffle is None or raffle.status not in {RaffleStatus.ACTIVE, RaffleStatus.CLOSED}:
        await send_no_raffle(bot, message.chat.id, runtime_settings)
        return
    await _send_my(
        session, bot, user=user, raffle=raffle, chat_id=message.chat.id, settings=runtime_settings
    )


@router.callback_query(F.data == "my:list")
async def on_my_list(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    runtime_settings: RuntimeSettings,
    now: datetime,
) -> None:
    await callback.answer()
    raffle = await current_raffle(session, now)
    if callback.message is None:
        return
    if raffle is None:
        await send_no_raffle(bot, callback.message.chat.id, runtime_settings)
        return
    await _send_my(
        session,
        bot,
        user=user,
        raffle=raffle,
        chat_id=callback.message.chat.id,
        settings=runtime_settings,
    )


async def _send_my(
    session: AsyncSession,
    bot: Bot,
    *,
    user: User,
    raffle,
    chat_id: int,
    settings: RuntimeSettings,
) -> None:
    amount, tickets, ranges = await applications_service.user_totals(session, raffle.id, user.id)
    summary = ru.my_summary_text(
        raffle_title=raffle.title,
        confirmed_amount=amount,
        tickets=tickets,
        ranges=ru.ranges_display(ranges),
    )
    applications = await apps_repo.for_user(session, raffle.id, user.id)
    if applications:
        summary += f"\n\nЗаявок: {len(applications)}\\."
    else:
        summary += "\n\nЗаявок пока нет\\."

    can_send_more = (
        raffle.status is RaffleStatus.ACTIVE
        and user.rules_accepted_at is not None
        and has_valid_consent(user, settings)
    )
    await notifications.send_text(
        bot,
        chat_id,
        summary,
        reply_markup=my_applications_keyboard(can_send_more=can_send_more),
    )
    for application in applications[:10]:
        await notifications.send_text(
            bot,
            chat_id,
            ru.application_line(
                public_id=application.public_id,
                organization=application.organization.name,
                method=application.payment_method_title,
                amount=application.amount,
                status=ru.STATUS_LABELS[application.status.value],
                created_at=application.created_at,
                tz=settings.timezone,
            ),
            reply_markup=_application_keyboard(application),
        )


@router.callback_query(F.data.startswith("my:app:"))
async def on_application_details(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    runtime_settings: RuntimeSettings,
) -> None:
    application_id = int(callback.data.rsplit(":", 1)[1])
    application = await apps_repo.get(session, application_id)
    if callback.message is None:
        return
    if application is None or application.user_id != user.id:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await callback.answer()
    text = _application_details(application, runtime_settings)
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        text,
        reply_markup=_application_keyboard(application),
    )


def _application_keyboard(application) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="ℹ️ Открыть заявку", callback_data=f"my:app:{application.id}")]
    ]
    if application.status is ApplicationStatus.REVISION_REQUESTED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📎 Загрузить новый чек",
                    callback_data=f"my:upload:{application.id}",
                )
            ]
        )
    if application.status.is_cancellable_by_user:
        rows.append(
            [
                InlineKeyboardButton(
                    text="❌ Отменить заявку",
                    callback_data=f"my:cancel:{application.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _amount_text(amount) -> str:
    return escape_markdown_v2(format_money(amount)) if amount is not None else "—"


def _application_details(application, settings: RuntimeSettings) -> str:
    lines = [
        f"🏢 *{escape_markdown_v2(application.organization.name)}*",
        f"Заявка `{escape_markdown_v2(application.public_id)}`",
        f"Способ оплаты: {escape_markdown_v2(application.payment_method_title or '—')}",
        f"Сумма: {_amount_text(application.amount)} BYN",
        f"Статус: {escape_markdown_v2(ru.STATUS_LABELS[application.status.value])}",
    ]
    if application.status is ApplicationStatus.CONFIRMED:
        lines.append(
            f"Номера по заявке: {application.first_ticket_number}–{application.last_ticket_number}"
            if application.tickets_awarded > 1
            else f"Номер по заявке: {application.first_ticket_number}"
        )
        lines.append(f"Всего билетов после неё: {application.tickets_total_after}")
    if application.status is ApplicationStatus.REJECTED and application.rejection_reason:
        lines.append(f"Причина: {escape_markdown_v2(application.rejection_reason)}")
        if settings.support_contact:
            lines.append(f"Поддержка: {escape_markdown_v2(settings.support_contact)}")
    if application.status is ApplicationStatus.VOIDED:
        lines.append(
            "Подтверждение аннулировано, номера "
            f"{application.voided_first_ticket_number}–{application.voided_last_ticket_number}"
        )
    return "\n".join(lines)


@router.callback_query(F.data.startswith("my:cancel:"))
async def on_cancel_request(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, user: User
) -> None:
    application_id = int(callback.data.rsplit(":", 1)[1])
    application = await apps_repo.get(session, application_id)
    if callback.message is None:
        return
    if application is None or application.user_id != user.id:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        f"Отменить заявку `{escape_markdown_v2(application.public_id)}`? "
        "Если проверяющий уже подтвердил оплату, отмена не сработает\\.",
        reply_markup=cancel_confirmation(application.id),
    )


@router.callback_query(F.data.startswith("my:cancel_yes:"))
async def on_cancel_confirm(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    now: datetime,
) -> None:
    application_id = int(callback.data.rsplit(":", 1)[1])
    application = await apps_repo.get(session, application_id)
    if callback.message is None:
        return
    if application is None or application.user_id != user.id:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    try:
        cancelled = await applications_service.cancel_by_user(
            session, application=application, user_id=user.id, now=now
        )
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer("Заявка отменена")
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        f"Заявка `{escape_markdown_v2(cancelled.public_id)}` отменена\\.",
    )
    if cancelled.card_chat_id and cancelled.card_message_id:
        from bot.services.cards import edit_review_card

        await edit_review_card(
            bot,
            cancelled,
            f"🚫 *Заявка {escape_markdown_v2(cancelled.public_id)} отменена участником*",
            caption=cancelled.receipt_type is not None,
        )


@router.callback_query(F.data.startswith("my:upload:"))
async def on_upload_new_receipt(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    runtime_settings: RuntimeSettings,
) -> None:
    application_id = int(callback.data.rsplit(":", 1)[1])
    application = await apps_repo.get(session, application_id)
    if callback.message is None:
        return
    if (
        application is None
        or application.user_id != user.id
        or application.status is not ApplicationStatus.REVISION_REQUESTED
    ):
        await callback.answer("Загрузить чек сейчас нельзя", show_alert=True)
        return
    await state.set_state(ParticipantFlow.receipt)
    await state.update_data(application_id=application.id)
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        ru.revision_request_text(application.public_id)
        + f"\n\nМаксимальный размер файла — {runtime_settings.receipt_max_mb} МБ\\.",
    )
