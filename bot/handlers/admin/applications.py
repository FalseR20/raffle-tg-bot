"""Application and participant views in the admin panel."""

from __future__ import annotations

from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ApplicationStatus
from bot.db.models import Application
from bot.db.repositories import applications as apps_repo
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import participants as participants_repo
from bot.db.repositories import raffles as raffles_repo
from bot.keyboards.admin import (
    application_card,
    applications_menu,
    participants_list,
)
from bot.services import applications as applications_service
from bot.services import notifications
from bot.services.cards import CardDeliveryError, send_receipt_to_chat
from bot.services.permissions import Permissions
from bot.services.settings import RuntimeSettings
from bot.services.storage import ReceiptStorage
from bot.services.users import display_name, mention
from bot.states import ApplicationSearch
from bot.texts import ru
from bot.texts.markdown import escape_markdown_v2
from bot.utils.time import format_datetime, utcnow

router = Router(name="admin_applications")

STATUS_FILTERS: dict[str, tuple[ApplicationStatus, ...]] = {
    "pending": (ApplicationStatus.PENDING_REVIEW, ApplicationStatus.REVISION_REQUESTED),
    "confirmed": (ApplicationStatus.CONFIRMED,),
    "rejected": (ApplicationStatus.REJECTED,),
    "all": tuple(ApplicationStatus),
}


def _scope(permissions: Permissions) -> frozenset[int] | None:
    return permissions.allowed_organization_ids()


def _can_view(permissions: Permissions, application: Application) -> bool:
    allowed = _scope(permissions)
    return allowed is None or application.organization_id in allowed


def _money(value) -> str:
    return escape_markdown_v2(ru.format_money(value)) if value is not None else "—"


def parse_apps_filter(data: str) -> tuple[str, int]:
    """Parse ``adm:apps_<filter>[:<raffle_id>]`` into filter and raffle id."""
    parts = data.split(":")
    if len(parts) < 2 or not parts[1].startswith("apps_"):
        raise ValueError("not an applications filter callback")
    filter_key = parts[1].split("_", 1)[1]
    raffle_id = int(parts[2]) if len(parts) > 2 else 0
    return filter_key, raffle_id


@router.callback_query(F.data == "adm:apps")
async def on_apps_menu(callback: CallbackQuery, bot: Bot, permissions: Permissions) -> None:
    if callback.message is None:
        return
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "📥 *Заявки*\n\nВыберите фильтр:",
        reply_markup=applications_menu(),
    )


@router.callback_query(F.data.regexp(r"^adm:orgapps:\d+$"))
async def on_org_apps(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
) -> None:
    if callback.message is None or callback.data is None:
        return
    organization_id = int(callback.data.rsplit(":", 1)[1])
    if organization_id not in permissions.org_rep_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await _send_list(
        session,
        bot,
        chat_id=callback.message.chat.id,
        permissions=permissions,
        settings=runtime_settings,
        filter_key="all",
        raffle_id=None,
        offset=0,
    )


@router.callback_query(F.data.regexp(r"^adm:apps_(pending|confirmed|rejected|all)(:\d+)?$"))
async def on_apps_filter(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
) -> None:
    if callback.message is None or callback.data is None:
        return
    filter_key, raffle_id = parse_apps_filter(callback.data)
    await callback.answer()
    await _send_list(
        session,
        bot,
        chat_id=callback.message.chat.id,
        permissions=permissions,
        settings=runtime_settings,
        filter_key=filter_key,
        raffle_id=raffle_id or None,
        offset=0,
    )


@router.callback_query(F.data.regexp(r"^adm:apps_page:(pending|confirmed|rejected|all):\d+:\d+$"))
async def on_apps_page(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
) -> None:
    if callback.message is None or callback.data is None:
        return
    _, _, filter_key, raffle_raw, offset_raw = callback.data.split(":")
    await callback.answer()
    await _send_list(
        session,
        bot,
        chat_id=callback.message.chat.id,
        permissions=permissions,
        settings=runtime_settings,
        filter_key=filter_key,
        raffle_id=int(raffle_raw) or None,
        offset=int(offset_raw),
    )


async def _send_list(
    session: AsyncSession,
    bot: Bot,
    *,
    chat_id: int,
    permissions: Permissions,
    settings: RuntimeSettings,
    filter_key: str,
    raffle_id: int | None,
    offset: int,
    query_text: str | None = None,
) -> None:
    if raffle_id is None:
        current = await raffles_repo.get_current(session)
        raffle_id = current.id if current else None
    filters = apps_repo.ApplicationFilters(
        raffle_id=raffle_id,
        statuses=STATUS_FILTERS.get(filter_key, STATUS_FILTERS["all"]),
        organization_ids=_scope(permissions),
        query=query_text,
    )
    total = await apps_repo.count_filtered(session, filters)
    applications = await apps_repo.list_filtered(session, filters, limit=10, offset=offset)
    if not applications:
        await notifications.send_text(
            bot,
            chat_id,
            "Заявок по этому фильтру нет\\.",
            reply_markup=applications_menu(),
        )
        return
    lines = []
    for application in applications:
        lines.append(
            f"`{escape_markdown_v2(application.public_id)}` · "
            f"{escape_markdown_v2(display_name(application.user))} · "
            f"{escape_markdown_v2(application.organization.name)}\n"
            f"{_money(application.amount)} BYN · "
            f"{escape_markdown_v2(ru.STATUS_LABELS[application.status.value])} · "
            f"{escape_markdown_v2(format_datetime(application.created_at, settings.timezone))}"
        )
    text = (
        f"📥 Заявки ({total})\\."
        + (" Поиск: " + escape_markdown_v2(query_text) if query_text else "")
        + "\n\n"
        + "\n\n".join(lines)
    )
    rows = [
        [
            InlineKeyboardButton(
                text=f"{application.public_id} · {display_name(application.user)[:25]}",
                callback_data=f"adm:app:{application.id}",
            )
        ]
        for application in applications
    ]
    nav = []
    if offset > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"adm:apps_page:{filter_key}:{raffle_id or 0}:{max(0, offset - 10)}",
            )
        )
    if offset + 10 < total:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"adm:apps_page:{filter_key}:{raffle_id or 0}:{offset + 10}",
            )
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ К фильтрам", callback_data="adm:apps")])
    await notifications.send_text(
        bot, chat_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.callback_query(F.data.regexp(r"^adm:app:\d+$"))
async def on_application_card(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
) -> None:
    if callback.message is None or callback.data is None:
        return
    application = await apps_repo.get(session, int(callback.data.rsplit(":", 1)[1]))
    if application is None or not _can_view(permissions, application):
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await callback.answer()
    text = await _application_card_text(session, application, runtime_settings)
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        text,
        reply_markup=application_card(application.id, status=application.status.value),
    )


async def _application_card_text(
    session: AsyncSession, application: Application, settings: RuntimeSettings
) -> str:
    user = application.user
    lines = [
        f"🆔 Заявка `{escape_markdown_v2(application.public_id)}`",
        f"🔖 Код платежа: `{escape_markdown_v2(application.payment_code)}`",
        f"👤 {mention(user)} · ID {user.id}",
        f"🏢 {escape_markdown_v2(application.organization.name)}",
        f"💳 {escape_markdown_v2(application.payment_method_title or '—')}",
        f"💰 {_money(application.amount)} BYN",
        f"📊 Статус: *{escape_markdown_v2(ru.STATUS_LABELS[application.status.value])}*",
        f"📅 Создана: "
        f"{escape_markdown_v2(format_datetime(application.created_at, settings.timezone))}",
        f"📤 Отправлена: "
        f"{escape_markdown_v2(format_datetime(application.submitted_at, settings.timezone))}",
    ]
    if application.status is ApplicationStatus.CONFIRMED:
        lines.append(
            f"🎫 Выдано: +{application.tickets_awarded} "
            f"(всего {application.tickets_total_after}) · "
            f"{application.first_ticket_number}–{application.last_ticket_number}"
        )
        lines.append(
            f"✅ Проверил: {application.reviewed_by} · "
            f"{format_datetime(application.reviewed_at, settings.timezone)}"
        )
    if application.status is ApplicationStatus.VOIDED:
        lines.append(
            "♻️ Аннулированы номера "
            f"{application.voided_first_ticket_number}–{application.voided_last_ticket_number}"
        )
    if application.rejection_reason:
        lines.append(f"❌ Причина: {escape_markdown_v2(application.rejection_reason)}")
    if (
        application.status
        in {ApplicationStatus.PENDING_REVIEW, ApplicationStatus.REVISION_REQUESTED}
        and application.submitted_at is not None
    ):
        age_days = (utcnow() - application.submitted_at).days
        lines.append(f"⏳ На проверке: {age_days} дн\\.")
    if application.receipt_object_key:
        lines.append(
            f"📎 Чек: {escape_markdown_v2(application.receipt_mime or '—')}, "
            f"{application.receipt_size or 0} байт"
        )
        lines.append(f"🗄 Ключ: `{escape_markdown_v2(application.receipt_object_key)}`")
    if application.payment_instructions_snapshot:
        lines.append(
            "\n💳 *Инструкция на момент заявки:*\n" + application.payment_instructions_snapshot
        )
    history = await audit_repo.for_application(session, application.id, limit=6)
    if history:
        lines.append("\n🕘 *История:*")
        for entry in history:
            lines.append(
                f"{escape_markdown_v2(format_datetime(entry.created_at, settings.timezone))} · "
                f"{escape_markdown_v2(entry.action)}"
            )
    if application.notification_error:
        lines.append(
            f"\n⚠️ Ошибка уведомления: {escape_markdown_v2(application.notification_error[:200])}"
        )
    return "\n".join(lines)


@router.callback_query(F.data.regexp(r"^adm:app_file:\d+$"))
async def on_application_file(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    storage: ReceiptStorage,
) -> None:
    if callback.message is None or callback.data is None:
        return
    application = await apps_repo.get(session, int(callback.data.rsplit(":", 1)[1]))
    if application is None or not _can_view(permissions, application):
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    try:
        await send_receipt_to_chat(
            bot, chat_id=callback.message.chat.id, application=application, storage=storage
        )
    except CardDeliveryError as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return
    await callback.answer("Чек отправлен")


@router.callback_query(F.data.regexp(r"^adm:app_notify:\d+$"))
async def on_app_notify(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
) -> None:
    if callback.message is None or callback.data is None:
        return
    application = await apps_repo.get(session, int(callback.data.rsplit(":", 1)[1]))
    if application is None or not _can_view(permissions, application):
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if application.status is ApplicationStatus.CONFIRMED:
        total, tickets, ranges = await applications_service.user_totals(
            session, application.raffle_id, application.user_id
        )
        text = ru.user_confirmation_text(
            public_id=application.public_id,
            amount=application.amount or Decimal("0.00"),
            organization_name=application.organization.name,
            total_amount=total,
            new_numbers=(
                str(application.first_ticket_number)
                if application.tickets_awarded == 1
                else f"{application.first_ticket_number}–{application.last_ticket_number}"
            ),
            total_tickets=tickets,
            all_numbers=ru.ranges_display(ranges),
            channel_title=runtime_settings.channel_title,
            channel_url=runtime_settings.channel_url,
        )
    elif application.status is ApplicationStatus.REJECTED:
        text = ru.rejection_text(
            public_id=application.public_id,
            reason=application.rejection_reason or "—",
            support_contact=runtime_settings.support_contact,
        )
    else:
        await callback.answer("Для этой заявки уведомление не предусмотрено", show_alert=True)
        return
    delivery = await notifications.send_text(bot, application.user_id, text)
    if delivery.ok:
        application.user_notified_at = utcnow()
        application.notification_error = None
    else:
        application.notification_error = (delivery.error or "unknown")[:500]
    await audit_repo.log(
        session,
        entity_type="application",
        entity_id=application.id,
        application_id=application.id,
        raffle_id=application.raffle_id,
        actor_id=permissions.user_id,
        action="user_notification_resent",
        payload={"delivered": delivery.ok},
        at=utcnow(),
    )
    await session.commit()
    await callback.answer("Уведомление отправлено" if delivery.ok else "Не удалось отправить")


@router.callback_query(F.data.regexp(r"^adm:app_remind:\d+$"))
async def on_remind(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
) -> None:
    if callback.message is None or callback.data is None:
        return
    application = await apps_repo.get(session, int(callback.data.rsplit(":", 1)[1]))
    if application is None or not _can_view(permissions, application):
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if application.status not in {
        ApplicationStatus.PENDING_REVIEW,
        ApplicationStatus.REVISION_REQUESTED,
    }:
        await callback.answer("Заявка уже обработана", show_alert=True)
        return
    chat_id = application.card_chat_id or application.organization.review_chat_id
    thread_id = None if application.card_chat_id else application.organization.review_thread_id
    delivery = await notifications.send_text(
        bot,
        chat_id,
        f"🔔 Напоминание: заявка `{escape_markdown_v2(application.public_id)}` ожидает проверки\\.",
        message_thread_id=thread_id,
    )
    await audit_repo.log(
        session,
        entity_type="application",
        entity_id=application.id,
        application_id=application.id,
        raffle_id=application.raffle_id,
        actor_id=permissions.user_id,
        action="reviewer_reminded",
        payload={"chat_id": chat_id, "delivered": delivery.ok},
        at=utcnow(),
    )
    await session.commit()
    if delivery.ok:
        await callback.answer("Напоминание отправлено")
    else:
        await callback.answer("Не удалось отправить напоминание", show_alert=True)


@router.callback_query(F.data == "adm:app_search")
async def on_search_prompt(callback: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.set_state(ApplicationSearch.query)
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "Введите ID заявки, код платежа, Telegram ID или username:",
    )


@router.message(ApplicationSearch.query, F.text)
async def on_search_query(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
    state: FSMContext,
) -> None:
    query = (message.text or "").strip()
    await state.clear()
    if not query:
        await notifications.send_text(bot, message.chat.id, "Пустой запрос\\.")
        return
    await _send_list(
        session,
        bot,
        chat_id=message.chat.id,
        permissions=permissions,
        settings=runtime_settings,
        filter_key="all",
        raffle_id=None,
        offset=0,
        query_text=query,
    )


@router.callback_query(F.data == "adm:participants")
async def on_participants(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
) -> None:
    if callback.message is None:
        return
    if permissions.is_org_rep and not permissions.can_manage_raffles:
        await callback.answer("Нет доступа", show_alert=True)
        return
    raffle = await raffles_repo.get_current(session)
    if raffle is None:
        await callback.answer("Нет текущего розыгрыша", show_alert=True)
        return
    participants = await participants_repo.list_for_raffle(session, raffle.id, limit=30)
    await callback.answer()
    if not participants:
        await notifications.send_text(bot, callback.message.chat.id, "Участников пока нет\\.")
        return
    lines = [
        f"👥 *Участники: {escape_markdown_v2(raffle.title)}*",
        f"Всего: {await participants_repo.count_for_raffle(session, raffle.id)}",
        f"Билеты: {raffle.last_ticket_number}\n",
    ]
    for total in participants:
        lines.append(
            f"{escape_markdown_v2(display_name(total.user))} · ID {total.user_id} · "
            f"{escape_markdown_v2(ru.format_money(total.confirmed_amount))} BYN · "
            f"{total.tickets_issued} билетов"
        )
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "\n".join(lines),
        reply_markup=participants_list(
            [(total.user_id, display_name(total.user)) for total in participants]
        ),
    )


@router.callback_query(F.data.regexp(r"^adm:part:\d+$"))
async def on_participant(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
) -> None:
    if callback.message is None or callback.data is None:
        return
    if not permissions.can_manage_raffles:
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    raffle = await raffles_repo.get_current(session)
    if raffle is None:
        await callback.answer("Нет текущего розыгрыша", show_alert=True)
        return
    total = await participants_repo.get(session, raffle.id, user_id)
    applications = await apps_repo.for_user(session, raffle.id, user_id)
    if total is None and not applications:
        await callback.answer("Участник не найден", show_alert=True)
        return
    await callback.answer()
    ranges = await apps_repo.confirmed_ranges(session, raffle.id, user_id)
    lines = [
        f"👤 ID {user_id}",
        f"Подтверждено: {_money(total.confirmed_amount if total else 0)} BYN",
        f"Билеты: {total.tickets_issued if total else 0}",
        f"Номера: {escape_markdown_v2(ru.ranges_display(ranges))}",
        f"Заявок: {len(applications)}\n",
    ]
    for application in applications[:10]:
        lines.append(
            f"`{escape_markdown_v2(application.public_id)}` · "
            f"{escape_markdown_v2(application.organization.name)} · "
            f"{_money(application.amount)} BYN · "
            f"{escape_markdown_v2(ru.STATUS_LABELS[application.status.value])}"
        )
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К участникам", callback_data="adm:participants")]
            ]
        ),
    )
