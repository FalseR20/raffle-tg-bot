"""Review chat callbacks: confirm, reject, request a new receipt, void."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ApplicationStatus
from bot.db.models import User
from bot.db.repositories import applications as apps_repo
from bot.db.repositories import participants as participants_repo
from bot.domain.money import format_money
from bot.domain.tickets import build_preview
from bot.keyboards.participant import channel_keyboard
from bot.keyboards.review import confirm_second_step, reject_reasons, void_confirm
from bot.services import notifications
from bot.services.cards import edit_review_card
from bot.services.errors import (
    ServiceError,
)
from bot.services.permissions import Permissions
from bot.services.reviews import (
    ConfirmationResult,
    confirm_application,
    reject_application,
    request_new_receipt,
    void_application,
)
from bot.services.settings import RuntimeSettings
from bot.services.users import display_name
from bot.states import ReviewFlow
from bot.texts import ru
from bot.texts.markdown import escape_markdown_v2

router = Router(name="review")

REJECTION_LABELS = {
    "unreadable": "чек не читается",
    "not_found": "платёж не найден",
    "amount_mismatch": "сумма не совпадает",
    "wrong_organization": "перевод в другую организацию",
    "duplicate": "чек уже был использован",
}


async def _load_application(
    session: AsyncSession, callback: CallbackQuery, permissions: Permissions
) -> tuple:
    if callback.data is None or callback.message is None:
        return None, None
    application_id = int(callback.data.rsplit(":", 1)[1])
    application = await apps_repo.get(session, application_id)
    if application is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return None, None
    if not permissions.can_review_organization(application.organization_id):
        await callback.answer("Нет доступа к этой заявке", show_alert=True)
        return None, None
    if (
        callback.message.chat.id != application.card_chat_id
        and callback.message.chat.type != "private"
    ):
        await callback.answer(
            "Действие доступно только в чате, куда отправлена карточка", show_alert=True
        )
        return None, None
    return application, application_id


@router.callback_query(F.data.startswith("rev:confirm:"))
async def on_confirm_request(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
    env_settings,
) -> None:
    application, application_id = await _load_application(session, callback, permissions)
    if application is None:
        return
    if application.status is not ApplicationStatus.PENDING_REVIEW:
        await callback.answer("Заявка уже обработана", show_alert=True)
        return
    if application.user_id == user.id:
        await callback.answer("Нельзя подтверждать собственную заявку", show_alert=True)
        return
    if not runtime_settings.confirm_two_step:
        await _confirm(
            callback,
            bot,
            session,
            user,
            permissions,
            runtime_settings,
            env_settings,
            application_id,
        )
        return

    total = await participants_repo.get(session, application.raffle_id, application.user_id)
    current_amount = total.confirmed_amount if total else Decimal("0.00")
    current_tickets = total.tickets_issued if total else 0
    preview = build_preview(
        application.amount or Decimal("0.00"),
        current_amount=current_amount,
        current_tickets=current_tickets,
        ticket_price=application.raffle.ticket_price,
    )
    await callback.answer()
    if callback.message:
        text = (
            f"Подтвердить оплату по заявке `{escape_markdown_v2(application.public_id)}`?\n"
            f"Сумма {escape_markdown_v2(format_money(application.amount or 0))} BYN\\. "
            f"С учётом предыдущих подтверждённых платежей участника будет начислено: "
            f"\\+{ru.tickets_label(preview.delta)} \\(всего "
            f"{ru.tickets_label(preview.preview_tickets)}\\)\\.\n\n"
            "Итог пересчитывается в момент подтверждения\\."
        )
        await notifications.send_text(
            bot,
            callback.message.chat.id,
            text,
            reply_markup=confirm_second_step(application_id),
        )


@router.callback_query(F.data.startswith("rev:confirm_yes:"))
async def on_confirm_yes(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
    env_settings,
) -> None:
    application, application_id = await _load_application(session, callback, permissions)
    if application is None:
        return
    await _confirm(
        callback,
        bot,
        session,
        user,
        permissions,
        runtime_settings,
        env_settings,
        application_id,
    )


async def _confirm(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
    env_settings,
    application_id: int,
) -> None:
    now = datetime.now().astimezone()
    try:
        result = await confirm_application(
            session,
            application_id=application_id,
            reviewer_id=user.id,
            reviewer=permissions,
            now=now,
        )
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return

    await callback.answer("Номера выданы")
    await _after_confirmation(
        session,
        bot,
        result=result,
        reviewer_name=display_name(user),
        settings=runtime_settings,
        admin_ids=env_settings.admin_id_set,
    )


async def _after_confirmation(
    session: AsyncSession,
    bot: Bot,
    *,
    result: ConfirmationResult,
    reviewer_name: str,
    settings: RuntimeSettings,
    admin_ids,
) -> None:
    application = result.application
    ranges = ru.ranges_display(result.all_ranges)
    card_text = ru.confirmed_card_text(
        public_id=application.public_id,
        reviewer=reviewer_name,
        confirmed_at=application.reviewed_at or datetime.now().astimezone(),
        tz=settings.timezone,
        ranges=str(result.range),
        total_tickets=result.total_tickets,
    )
    await edit_review_card(bot, application, card_text)

    text = ru.user_confirmation_text(
        public_id=application.public_id,
        amount=application.amount or Decimal("0.00"),
        organization_name=application.organization.name,
        total_amount=result.total_amount,
        new_numbers=str(result.range),
        total_tickets=result.total_tickets,
        all_numbers=ranges,
        channel_title=settings.channel_title,
        channel_url=settings.channel_url,
    )
    delivery = await notifications.send_text(
        bot,
        application.user_id,
        text,
        reply_markup=channel_keyboard(settings.channel_url, settings.channel_title),
    )
    if delivery.ok:
        application.user_notified_at = datetime.now().astimezone()
        application.notification_error = None
    else:
        application.notification_error = delivery.error[:500] if delivery.error else "unknown"
        await notifications.notify_admins(
            bot,
            admin_ids,
            "⚠️ Не удалось уведомить участника о подтверждении заявки "
            f"`{application.public_id}`\\. Номера сохранены, уведомление будет повторено\\.",
        )
    await session.commit()


@router.callback_query(F.data.startswith("rev:reject:"))
async def on_reject_request(
    callback: CallbackQuery, session: AsyncSession, permissions: Permissions
) -> None:
    application, application_id = await _load_application(session, callback, permissions)
    if application is None:
        return
    if application.status is not ApplicationStatus.PENDING_REVIEW:
        await callback.answer("Заявка уже обработана", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Укажите причину отклонения:",
            reply_markup=reject_reasons(application_id),
        )


@router.callback_query(F.data.startswith("rev:reason:"))
async def on_reject_reason(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
    env_settings,
    state: FSMContext,
) -> None:
    if callback.data is None or callback.message is None:
        return
    _, _, application_id_raw, code = callback.data.split(":")
    application_id = int(application_id_raw)
    application = await apps_repo.get(session, application_id)
    if application is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if not permissions.can_review_organization(application.organization_id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    if (
        callback.message is not None
        and callback.message.chat.id != application.card_chat_id
        and callback.message.chat.type != "private"
    ):
        await callback.answer(
            "Действие доступно только в чате, куда отправлена карточка", show_alert=True
        )
        return
    if code == "other":
        await state.set_state(ReviewFlow.reject_reason)
        await state.update_data(application_id=application_id)
        await callback.answer()
        await callback.message.answer("Напишите причину отклонения одним сообщением:")
        return
    reason = REJECTION_LABELS.get(code, "другое")
    await _reject(
        callback,
        bot,
        session,
        user,
        permissions,
        runtime_settings,
        env_settings,
        application_id,
        reason,
    )


@router.message(ReviewFlow.reject_reason, F.text)
async def on_reject_reason_text(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user: User,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
    env_settings,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    application_id = int(data.get("application_id", 0))
    reason = (message.text or "").strip()
    if not reason:
        await notifications.send_text(bot, message.chat.id, "Причина не может быть пустой\\.")
        return
    await state.clear()
    await _reject(
        None,
        bot,
        session,
        user,
        permissions,
        runtime_settings,
        env_settings,
        application_id,
        reason,
        chat_id=message.chat.id,
    )


async def _reject(
    callback: CallbackQuery | None,
    bot: Bot,
    session: AsyncSession,
    user: User,
    permissions: Permissions,
    settings: RuntimeSettings,
    env_settings,
    application_id: int,
    reason: str,
    *,
    chat_id: int | None = None,
) -> None:
    try:
        result = await reject_application(
            session,
            application_id=application_id,
            reviewer_id=user.id,
            reviewer=permissions,
            reason=reason,
            now=datetime.now().astimezone(),
        )
        await session.commit()
    except ServiceError as exc:
        if callback:
            await callback.answer(exc.user_message, show_alert=True)
        elif chat_id:
            await notifications.send_text(bot, chat_id, escape_markdown_v2(exc.user_message))
        return

    if callback:
        await callback.answer("Заявка отклонена")
    application = result.application
    card_text = ru.rejected_card_text(
        public_id=application.public_id,
        reason=reason,
        reviewer=display_name(user),
        rejected_at=application.reviewed_at or datetime.now().astimezone(),
        tz=settings.timezone,
    )
    await edit_review_card(bot, application, card_text)
    delivery = await notifications.send_text(
        bot,
        application.user_id,
        ru.rejection_text(
            public_id=application.public_id,
            reason=reason,
            support_contact=settings.support_contact,
        ),
    )
    if not delivery.ok:
        application.notification_error = delivery.error[:500] if delivery.error else "unknown"
        await notifications.notify_admins(
            bot,
            env_settings.admin_id_set,
            f"⚠️ Не удалось уведомить участника об отклонении `{application.public_id}`\\.",
        )
    await session.commit()


@router.callback_query(F.data.startswith("rev:revision:"))
async def on_revision_request(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
) -> None:
    application, application_id = await _load_application(session, callback, permissions)
    if application is None:
        return
    try:
        result = await request_new_receipt(
            session,
            application_id=application_id,
            reviewer_id=user.id,
            reviewer=permissions,
            reason="запрошен новый чек",
            now=datetime.now().astimezone(),
        )
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer("Запрошен новый чек")
    refreshed = result.application
    await edit_review_card(
        bot,
        refreshed,
        f"📎 *По заявке {escape_markdown_v2(refreshed.public_id)} запрошен новый чек*",
    )
    await notifications.send_text(
        bot,
        refreshed.user_id,
        ru.revision_request_text(refreshed.public_id),
        reply_markup=_upload_keyboard(refreshed.id),
    )


def _upload_keyboard(application_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📎 Загрузить новый чек", callback_data=f"my:upload:{application_id}"
                )
            ]
        ]
    )


@router.callback_query(F.data.startswith("rev:void:"))
async def on_void_request(
    callback: CallbackQuery, session: AsyncSession, permissions: Permissions
) -> None:
    application, application_id = await _load_application(session, callback, permissions)
    if application is None:
        return
    if not permissions.can_manage_raffles:
        await callback.answer("Аннулировать может только организатор", show_alert=True)
        return
    if application.status is not ApplicationStatus.CONFIRMED:
        await callback.answer("Заявка не подтверждена", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"Аннулировать подтверждение по заявке `{escape_markdown_v2(application.public_id)}`? "
            "Это разрешено только для последнего выданного диапазона номеров\\.",
            reply_markup=void_confirm(application_id),
        )


@router.callback_query(F.data.startswith("rev:void_yes:"))
async def on_void_confirm(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
) -> None:
    application, application_id = await _load_application(session, callback, permissions)
    if application is None:
        return
    try:
        result = await void_application(
            session,
            application_id=application_id,
            actor_id=user.id,
            actor=permissions,
            reason="аннулирование организатором",
            now=datetime.now().astimezone(),
        )
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer("Подтверждение аннулировано")
    voided = result.application
    await edit_review_card(
        bot,
        voided,
        "♻️ *Подтверждение аннулировано*\n\n"
        f"Заявка `{escape_markdown_v2(voided.public_id)}`\\. "
        f"Номера {result.voided_range} освобождены\\.",
    )
    await notifications.send_text(
        bot,
        voided.user_id,
        f"♻️ Подтверждение оплаты по заявке №{escape_markdown_v2(voided.public_id)} "
        f"аннулировано\\. Номера {result.voided_range} больше не участвуют\\.",
    )


@router.callback_query(F.data.startswith("rev:cancel:"))
async def on_cancel_action(callback: CallbackQuery) -> None:
    await callback.answer("Отменено")
