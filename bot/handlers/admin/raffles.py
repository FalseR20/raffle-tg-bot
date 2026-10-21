"""Raffle management in the admin panel."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ApplicationStatus, RaffleStatus
from bot.db.models import Raffle
from bot.db.repositories import applications as apps_repo
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import organizations as orgs_repo
from bot.db.repositories import participants as participants_repo
from bot.db.repositories import raffles as raffles_repo
from bot.handlers.admin.common import parse_datetime, parse_decimal
from bot.keyboards.admin import (
    confirm_action,
    edit_organizations,
    finish_confirmation,
    raffle_card,
    raffle_edit_fields,
    raffles_list,
    wizard_cancel,
    wizard_organizations,
)
from bot.services import notifications
from bot.services.errors import ServiceError
from bot.services.permissions import Permissions
from bot.services.raffles import (
    RaffleInput,
    activate,
    archive,
    create_raffle,
    delete_draft,
    duplicate,
    extend_end_at,
    finish,
    resume,
    stop_acceptance,
    update_raffle,
    validate_input,
)
from bot.states import RaffleEdit, RaffleWizard
from bot.texts import ru
from bot.texts.markdown import escape_markdown_v2
from bot.utils.time import format_datetime, utcnow

router = Router(name="admin_raffles")


def _guard(event, permissions: Permissions) -> bool:
    return permissions.can_manage_raffles


async def _send_raffles(
    bot: Bot, chat_id: int, session: AsyncSession, permissions: Permissions
) -> None:
    raffles = await raffles_repo.list_all(session, limit=20)
    await notifications.send_text(
        bot,
        chat_id,
        "🎗 *Розыгрыши*",
        reply_markup=raffles_list(
            [(raffle.id, raffle.title, raffle.status) for raffle in raffles],
            can_manage=permissions.can_manage_raffles,
        ),
    )


async def _raffle_card_text(session: AsyncSession, raffle: Raffle, settings) -> str:
    counts = await apps_repo.count_by_status(session, raffle.id)
    total = sum(counts.values())
    pending = counts.get(ApplicationStatus.PENDING_REVIEW, 0) + counts.get(
        ApplicationStatus.REVISION_REQUESTED, 0
    )
    confirmed = counts.get(ApplicationStatus.CONFIRMED, 0)
    amount = await participants_repo.sum_amount(session, raffle.id)
    organizations = [entry.organization.name for entry in raffle.organizations if entry.is_enabled]
    return (
        f"🎗 *{escape_markdown_v2(raffle.title)}*\n\n"
        f"Статус: *{escape_markdown_v2(ru.RAFFLE_STATUS_LABELS[raffle.status.value])}*\n"
        f"💰 Цена билета: {escape_markdown_v2(ru.format_money(raffle.ticket_price))} BYN\n"
        f"💵 Минимальная сумма: {escape_markdown_v2(ru.format_money(raffle.min_amount))} BYN\n"
        f"🏆 Призов: {raffle.prizes_count}\n"
        f"📅 Начало: {escape_markdown_v2(format_datetime(raffle.start_at, settings.timezone))}\n"
        f"📅 Окончание: {escape_markdown_v2(format_datetime(raffle.end_at, settings.timezone))}\n"
        f"🏢 Организации: {escape_markdown_v2(', '.join(organizations) or '—')}\n"
        f"📥 Заявки: {total} · на проверке: {pending} · подтверждено: {confirmed}\n"
        f"💳 Подтверждённая сумма: {escape_markdown_v2(ru.format_money(amount))} BYN\n"
        f"🎫 Билеты: {raffle.last_ticket_number}"
    )


async def _show_raffle(
    bot: Bot, chat_id: int, session: AsyncSession, raffle: Raffle, settings
) -> None:
    text = await _raffle_card_text(session, raffle, settings)
    await notifications.send_text(
        bot,
        chat_id,
        text,
        reply_markup=raffle_card(raffle.id, raffle.status, can_manage=True),
    )


async def _get_raffle(session: AsyncSession, callback: CallbackQuery) -> Raffle | None:
    if callback.data is None:
        return None
    raffle_id = int(callback.data.rsplit(":", 1)[1])
    raffle = await raffles_repo.get(session, raffle_id)
    if raffle is None:
        await callback.answer("Розыгрыш не найден", show_alert=True)
    return raffle


@router.callback_query(F.data == "adm:raffles")
async def on_list(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None:
        return
    if not _guard(callback, permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await _send_raffles(bot, callback.message.chat.id, session, permissions)


@router.callback_query(F.data.regexp(r"^adm:raffle:\d+$"))
async def on_raffle(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
) -> None:
    if callback.message is None:
        return
    if not _guard(callback, permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    raffle = await _get_raffle(session, callback)
    if raffle is None:
        return
    await callback.answer()
    await _show_raffle(bot, callback.message.chat.id, session, raffle, runtime_settings)


@router.callback_query(F.data == "adm:raffle_new")
async def on_new(
    callback: CallbackQuery,
    bot: Bot,
    permissions: Permissions,
    runtime_settings,
    state: FSMContext,
) -> None:
    if callback.message is None:
        return
    if not _guard(callback, permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(RaffleWizard.title)
    await state.update_data(mode="create")
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "Введите название розыгрыша:",
        reply_markup=wizard_cancel(),
    )


@router.callback_query(F.data.regexp(r"^adm:raffle_activate:\d+$"))
async def on_activate(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
) -> None:
    if callback.message is None:
        return
    raffle = await _get_raffle(session, callback)
    if raffle is None:
        return
    try:
        await activate(session, actor=permissions, raffle=raffle, now=utcnow())
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer("Розыгрыш активирован")
    await _show_raffle(bot, callback.message.chat.id, session, raffle, runtime_settings)


@router.callback_query(F.data.regexp(r"^adm:raffle_stop:\d+$"))
async def on_stop_request(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None:
        return
    raffle = await _get_raffle(session, callback)
    if raffle is None:
        return
    await callback.answer()
    await notifications.send_text(
        callback.bot,
        callback.message.chat.id,
        "Остановить приём заявок? Поданные заявки можно будет проверять дальше\\.",
        reply_markup=confirm_action(
            "⏹ Да, остановить",
            f"adm:raffle_stop_yes:{raffle.id}",
            f"adm:raffle:{raffle.id}",
        ),
    )


@router.callback_query(F.data.regexp(r"^adm:raffle_stop_yes:\d+$"))
async def on_stop(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
) -> None:
    if callback.message is None:
        return
    raffle = await _get_raffle(session, callback)
    if raffle is None:
        return
    try:
        await stop_acceptance(session, actor=permissions, raffle=raffle, now=utcnow())
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer("Приём остановлен")
    await _show_raffle(bot, callback.message.chat.id, session, raffle, runtime_settings)


@router.callback_query(F.data.regexp(r"^adm:raffle_resume:\d+$"))
async def on_resume(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
) -> None:
    if callback.message is None:
        return
    raffle = await _get_raffle(session, callback)
    if raffle is None:
        return
    try:
        await resume(session, actor=permissions, raffle=raffle, now=utcnow())
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer("Приём возобновлён")
    await _show_raffle(bot, callback.message.chat.id, session, raffle, runtime_settings)


@router.callback_query(F.data.regexp(r"^adm:raffle_finish:\d+$"))
async def on_finish_request(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None:
        return
    raffle = await _get_raffle(session, callback)
    if raffle is None:
        return
    pending = await apps_repo.pending_for_raffle(session, raffle.id)
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        f"Завершить розыгрыш «{escape_markdown_v2(raffle.title)}»?\n\n"
        f"Заявок на проверке: {len(pending)}\\.\n"
        "После завершения подтверждения и аннулирование невозможны\\.",
        reply_markup=finish_confirmation(raffle.id, len(pending)),
    )


@router.callback_query(F.data.regexp(r"^adm:raffle_finish_yes:\d+:[01]$"))
async def on_finish(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
) -> None:
    if callback.message is None or callback.data is None:
        return
    parts = callback.data.split(":")
    raffle = await raffles_repo.get(session, int(parts[2]))
    if raffle is None:
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return
    expire = callback.data.rsplit(":", 1)[1] == "1"
    try:
        result = await finish(
            session,
            actor=permissions,
            raffle=raffle,
            expire_pending=expire,
            now=utcnow(),
        )
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer("Розыгрыш завершён")
    for application in result.expired_applications:
        await notifications.send_text(
            bot,
            application.user_id,
            f"Заявка `{escape_markdown_v2(application.public_id)}` переведена в «истекла»: "
            "розыгрыш завершён\\.",
        )
    await _show_raffle(bot, callback.message.chat.id, session, raffle, runtime_settings)


@router.callback_query(F.data.regexp(r"^adm:raffle_archive:\d+$"))
async def on_archive_request(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None:
        return
    raffle = await _get_raffle(session, callback)
    if raffle is None:
        return
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "Отправить розыгрыш в архив? Участники больше не увидят его как текущий\\.",
        reply_markup=confirm_action(
            "🗄 Да, в архив",
            f"adm:raffle_archive_yes:{raffle.id}",
            f"adm:raffle:{raffle.id}",
        ),
    )


@router.callback_query(F.data.regexp(r"^adm:raffle_archive_yes:\d+$"))
async def on_archive(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
) -> None:
    if callback.message is None:
        return
    raffle = await _get_raffle(session, callback)
    if raffle is None:
        return
    try:
        await archive(session, actor=permissions, raffle=raffle, now=utcnow())
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer("В архиве")
    await _show_raffle(bot, callback.message.chat.id, session, raffle, runtime_settings)


@router.callback_query(F.data.regexp(r"^adm:raffle_dup:\d+$"))
async def on_duplicate(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
) -> None:
    if callback.message is None:
        return
    raffle = await _get_raffle(session, callback)
    if raffle is None:
        return
    try:
        copy = await duplicate(session, actor=permissions, raffle=raffle, now=utcnow())
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer("Копия создана")
    await _show_raffle(bot, callback.message.chat.id, session, copy, runtime_settings)


@router.callback_query(F.data.regexp(r"^adm:raffle_del:\d+$"))
async def on_delete_request(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None:
        return
    raffle = await _get_raffle(session, callback)
    if raffle is None:
        return
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        f"Удалить черновик «{escape_markdown_v2(raffle.title)}»?",
        reply_markup=confirm_action(
            "🗑 Да, удалить",
            f"adm:raffle_del_yes:{raffle.id}",
            f"adm:raffle:{raffle.id}",
        ),
    )


@router.callback_query(F.data.regexp(r"^adm:raffle_del_yes:\d+$"))
async def on_delete(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
) -> None:
    if callback.message is None:
        return
    raffle = await _get_raffle(session, callback)
    if raffle is None:
        return
    try:
        await delete_draft(session, actor=permissions, raffle=raffle, now=utcnow())
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer("Удалено")
    await _send_raffles(bot, callback.message.chat.id, session, permissions)


@router.callback_query(F.data.regexp(r"^adm:raffle_extend:\d+$"))
async def on_extend_request(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
    state: FSMContext,
) -> None:
    if callback.message is None:
        return
    raffle = await _get_raffle(session, callback)
    if raffle is None:
        return
    await state.set_state(RaffleWizard.extend_end)
    await state.update_data(raffle_id=raffle.id)
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "Введите новую дату окончания в формате ДД\\.ММ\\.ГГГГ ЧЧ:ММ\\.",
        reply_markup=wizard_cancel(f"adm:raffle:{raffle.id}"),
    )


@router.message(RaffleWizard.extend_end, F.text)
async def on_extend_value(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    raffle = await raffles_repo.get(session, int(data.get("raffle_id", 0)))
    if raffle is None:
        await state.clear()
        return
    try:
        new_end = parse_datetime(message.text or "", runtime_settings.timezone)
        await extend_end_at(
            session, actor=permissions, raffle=raffle, new_end_at=new_end, now=utcnow()
        )
        await session.commit()
    except ValueError as exc:
        await notifications.send_text(bot, message.chat.id, escape_markdown_v2(str(exc)))
        return
    except ServiceError as exc:
        await notifications.send_text(bot, message.chat.id, escape_markdown_v2(exc.user_message))
        return
    await state.clear()
    await _show_raffle(bot, message.chat.id, session, raffle, runtime_settings)


# ------------------------------- creation wizard ----------------------------

WIZARD_INPUT_STEPS = (
    RaffleWizard.title,
    RaffleWizard.description,
    RaffleWizard.ticket_price,
    RaffleWizard.min_amount,
    RaffleWizard.prizes_count,
    RaffleWizard.start_at,
    RaffleWizard.end_at,
    RaffleWizard.channel_url,
    RaffleWizard.channel_title,
    RaffleEdit.value,
)

_PROMPTS = {
    "title": "Введите название розыгрыша:",
    "description": "Введите описание и правила (обычный текст):",
    "ticket_price": "Введите цену билета в BYN, например 5,00:",
    "min_amount": "Введите минимальную сумму одной заявки или «-», чтобы взять цену билета:",
    "prizes_count": "Введите количество призов (целое число):",
    "start_at": "Введите дату и время начала: ДД.ММ.ГГГГ ЧЧ:ММ",
    "end_at": "Введите дату и время окончания: ДД.ММ.ГГГГ ЧЧ:ММ",
    "channel_url": "Введите ссылку на канал или «-», чтобы взять значение по умолчанию:",
    "channel_title": "Введите название канала или «-», чтобы взять значение по умолчанию:",
}


async def _prompt(bot: Bot, chat_id: int, step: str, state: FSMContext) -> None:
    await state.set_state(getattr(RaffleWizard, step))
    await notifications.send_text(bot, chat_id, _PROMPTS[step], reply_markup=wizard_cancel())


@router.message(StateFilter(*WIZARD_INPUT_STEPS), F.text)
async def on_wizard_input(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
    state: FSMContext,
) -> None:
    current = await state.get_state() or ""
    step = current.rsplit(":", 1)[-1]
    data = await state.get_data()
    raw = (message.text or "").strip()
    if not _guard(message, permissions):
        return

    if current.startswith("RaffleEdit"):
        await _handle_edit_value(
            message, bot, session, permissions, runtime_settings, state, data, raw
        )
        return

    if step == "title":
        if not raw:
            await notifications.send_text(bot, message.chat.id, "Название не может быть пустым\\.")
            return
        data["title"] = raw
        await state.update_data(**data)
        await _prompt(bot, message.chat.id, "description", state)
        return
    if step == "description":
        data["description"] = raw
        await state.update_data(**data)
        await _prompt(bot, message.chat.id, "ticket_price", state)
        return
    if step == "ticket_price":
        try:
            data["ticket_price"] = str(parse_decimal(raw))
        except ValueError as exc:
            await notifications.send_text(bot, message.chat.id, escape_markdown_v2(str(exc)))
            return
        await state.update_data(**data)
        await _prompt(bot, message.chat.id, "min_amount", state)
        return
    if step == "min_amount":
        try:
            data["min_amount"] = data["ticket_price"] if raw == "-" else str(parse_decimal(raw))
        except ValueError as exc:
            await notifications.send_text(bot, message.chat.id, escape_markdown_v2(str(exc)))
            return
        await state.update_data(**data)
        await _prompt(bot, message.chat.id, "prizes_count", state)
        return
    if step == "prizes_count":
        try:
            prizes = int(raw)
            if prizes < 1:
                raise ValueError
        except ValueError:
            await notifications.send_text(
                bot, message.chat.id, "Введите целое число не меньше 1\\."
            )
            return
        data["prizes_count"] = prizes
        await state.update_data(**data)
        await _prompt(bot, message.chat.id, "start_at", state)
        return
    if step == "start_at":
        try:
            data["start_at"] = parse_datetime(raw, runtime_settings.timezone).isoformat()
        except ValueError as exc:
            await notifications.send_text(bot, message.chat.id, escape_markdown_v2(str(exc)))
            return
        await state.update_data(**data)
        await _prompt(bot, message.chat.id, "end_at", state)
        return
    if step == "end_at":
        try:
            end_at = parse_datetime(raw, runtime_settings.timezone)
            start_at = datetime.fromisoformat(data["start_at"])
            if end_at <= start_at:
                raise ValueError("Дата окончания должна быть позже даты начала.")
        except ValueError as exc:
            await notifications.send_text(bot, message.chat.id, escape_markdown_v2(str(exc)))
            return
        data["end_at"] = end_at.isoformat()
        await state.update_data(**data)
        await _prompt(bot, message.chat.id, "channel_url", state)
        return
    if step == "channel_url":
        data["channel_url"] = runtime_settings.channel_url or "" if raw == "-" else raw
        await state.update_data(**data)
        await _prompt(bot, message.chat.id, "channel_title", state)
        return
    if step == "channel_title":
        data["channel_title"] = runtime_settings.channel_title or "" if raw == "-" else raw
        organizations = await orgs_repo.list_all(session, only_active=True)
        selected = [organization.id for organization in organizations]
        await state.update_data(
            channel_title=data["channel_title"],
            selected_organizations=selected,
        )
        await state.set_state(RaffleWizard.organizations)
        await notifications.send_text(
            bot,
            message.chat.id,
            "Выберите организации для розыгрыша:",
            reply_markup=wizard_organizations(
                [(organization.id, organization.name) for organization in organizations],
                set(selected),
            ),
        )
        return


@router.callback_query(F.data.startswith("rw:org:"))
async def on_wizard_org_toggle(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext, permissions: Permissions
) -> None:
    if callback.message is None or callback.data is None:
        return
    if not _guard(callback, permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    organization_id = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    selected = set(data.get("selected_organizations", []))
    if organization_id in selected:
        selected.remove(organization_id)
    else:
        selected.add(organization_id)
    await state.update_data(selected_organizations=sorted(selected))
    organizations = await orgs_repo.list_all(session, only_active=True)
    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=wizard_organizations(
                [(organization.id, organization.name) for organization in organizations],
                selected,
            )
        )


@router.callback_query(F.data == "rw:org_done")
async def on_wizard_save(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
    state: FSMContext,
) -> None:
    if callback.message is None:
        return
    if not _guard(callback, permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    organization_ids = list(data.get("selected_organizations", []))
    if not organization_ids:
        await callback.answer("Выберите хотя бы одну организацию", show_alert=True)
        return
    raffle_input = RaffleInput(
        title=data["title"],
        description=data.get("description", ""),
        ticket_price=Decimal(data["ticket_price"]),
        min_amount=Decimal(data["min_amount"]),
        prizes_count=int(data["prizes_count"]),
        start_at=datetime.fromisoformat(data["start_at"]),
        end_at=datetime.fromisoformat(data["end_at"]),
        channel_url=data.get("channel_url", ""),
        channel_title=data.get("channel_title", ""),
    )
    try:
        validate_input(raffle_input)
        raffle = await create_raffle(
            session,
            actor=permissions,
            data=raffle_input,
            organization_ids=organization_ids,
            now=utcnow(),
        )
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await state.clear()
    await callback.answer("Черновик создан")
    await _show_raffle(bot, callback.message.chat.id, session, raffle, runtime_settings)


@router.callback_query(F.data == "rw:cancel")
async def on_wizard_cancel(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    await state.clear()
    await callback.answer("Отменено")
    if callback.message:
        await _send_raffles(bot, callback.message.chat.id, session, permissions)


# ------------------------------- editing ------------------------------------


@router.callback_query(F.data.regexp(r"^adm:raffle_edit:\d+$"))
async def on_edit(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
) -> None:
    if callback.message is None:
        return
    raffle = await _get_raffle(session, callback)
    if raffle is None:
        return
    await callback.answer()
    locked = raffle.status.is_current or raffle.status.is_finished
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        f"✏️ Редактирование «{escape_markdown_v2(raffle.title)}»\n\n"
        "Выберите поле\\. Заблокированные после активации поля отмечены замком\\.",
        reply_markup=raffle_edit_fields(raffle.id, locked=locked),
    )


@router.callback_query(F.data.regexp(r"^adm:raffle_set:\d+:\w+$"))
async def on_edit_field(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    if callback.message is None or callback.data is None:
        return
    _, _, raffle_id_raw, field = callback.data.split(":")
    raffle = await raffles_repo.get(session, int(raffle_id_raw))
    if raffle is None:
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return
    if field == "organizations":
        if raffle.status is not RaffleStatus.DRAFT:
            await callback.answer(
                "Организации можно менять только в черновике. Верните розыгрыш в черновик.",
                show_alert=True,
            )
            return
        organizations = await orgs_repo.list_all(session, only_active=True)
        selected = {entry.organization_id for entry in raffle.organizations if entry.is_enabled}
        await state.set_state(RaffleEdit.organizations)
        await state.update_data(raffle_id=raffle.id, selected_organizations=sorted(selected))
        await callback.answer()
        await notifications.send_text(
            bot,
            callback.message.chat.id,
            "Выберите организации:",
            reply_markup=edit_organizations(
                raffle.id,
                [(organization.id, organization.name) for organization in organizations],
                selected,
            ),
        )
        return
    await state.set_state(RaffleEdit.value)
    await state.update_data(raffle_id=raffle.id, field=field)
    await callback.answer()
    prompt = {
        "title": "Введите новое название:",
        "description": "Введите новое описание:",
        "ticket_price": "Введите новую цену билета:",
        "min_amount": "Введите новую минимальную сумму:",
        "prizes_count": "Введите новое количество призов:",
        "end_at": "Введите новую дату окончания: ДД.ММ.ГГГГ ЧЧ:ММ",
        "channel_url": "Введите ссылку на канал:",
        "channel_title": "Введите название канала:",
    }.get(field, "Введите новое значение:")
    await notifications.send_text(bot, callback.message.chat.id, prompt)


async def _handle_edit_value(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
    state: FSMContext,
    data: dict,
    raw: str,
) -> None:
    raffle = await raffles_repo.get(session, int(data.get("raffle_id", 0)))
    field = data.get("field")
    if raffle is None or not field:
        await state.clear()
        return
    now = utcnow()
    try:
        if field == "end_at":
            new_end = parse_datetime(raw, runtime_settings.timezone)
            await extend_end_at(
                session, actor=permissions, raffle=raffle, new_end_at=new_end, now=now
            )
        else:
            raffle_input = RaffleInput(
                title=raffle.title,
                description=raffle.description,
                ticket_price=raffle.ticket_price,
                min_amount=raffle.min_amount,
                prizes_count=raffle.prizes_count,
                start_at=raffle.start_at,
                end_at=raffle.end_at,
                channel_url=raffle.channel_url,
                channel_title=raffle.channel_title,
                rules_text=raffle.rules_text,
            )
            if field == "title":
                raffle_input = _replace(raffle_input, title=raw)
            elif field == "description":
                raffle_input = _replace(raffle_input, description=raw)
            elif field == "ticket_price":
                raffle_input = _replace(raffle_input, ticket_price=parse_decimal(raw))
            elif field == "min_amount":
                raffle_input = _replace(raffle_input, min_amount=parse_decimal(raw))
            elif field == "prizes_count":
                raffle_input = _replace(raffle_input, prizes_count=int(raw))
            elif field == "channel_url":
                raffle_input = _replace(raffle_input, channel_url=raw)
            elif field == "channel_title":
                raffle_input = _replace(raffle_input, channel_title=raw)
            else:
                raise ValueError("Неизвестное поле.")
            await update_raffle(session, actor=permissions, raffle=raffle, data=raffle_input)
        await audit_repo.log(
            session,
            entity_type="raffle",
            entity_id=raffle.id,
            raffle_id=raffle.id,
            actor_id=permissions.user_id,
            action="raffle_edited",
            payload={"field": field},
            at=now,
        )
        await session.commit()
    except (ValueError, ServiceError) as exc:
        text = exc.user_message if isinstance(exc, ServiceError) else str(exc)
        await notifications.send_text(bot, message.chat.id, escape_markdown_v2(text))
        return
    await state.clear()
    await _show_raffle(bot, message.chat.id, session, raffle, runtime_settings)


def _replace(raffle_input: RaffleInput, **changes):
    from dataclasses import replace

    return replace(raffle_input, **changes)


@router.callback_query(F.data.startswith("rw:edit_org:"))
async def on_edit_org_toggle(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    if callback.message is None or callback.data is None:
        return
    organization_id = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    selected = set(data.get("selected_organizations", []))
    if organization_id in selected:
        selected.remove(organization_id)
    else:
        selected.add(organization_id)
    await state.update_data(selected_organizations=sorted(selected))
    organizations = await orgs_repo.list_all(session, only_active=True)
    await callback.answer()
    from bot.keyboards.admin import edit_organizations

    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=edit_organizations(
                int(data.get("raffle_id", 0)),
                [(organization.id, organization.name) for organization in organizations],
                selected,
            )
        )


@router.callback_query(F.data.regexp(r"^rw:edit_org_done:\d+$"))
async def on_edit_org_save(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings,
    state: FSMContext,
) -> None:
    if callback.message is None or callback.data is None:
        return
    raffle_id = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    selected = list(data.get("selected_organizations", []))
    if not selected:
        await callback.answer("Выберите хотя бы одну организацию", show_alert=True)
        return
    raffle = await raffles_repo.get(session, raffle_id)
    if raffle is None:
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return
    await raffles_repo.set_organizations(session, raffle.id, selected)
    await audit_repo.log(
        session,
        entity_type="raffle",
        entity_id=raffle.id,
        raffle_id=raffle.id,
        actor_id=permissions.user_id,
        action="raffle_edited",
        payload={"field": "organizations", "organization_ids": selected},
        at=utcnow(),
    )
    await session.commit()
    await state.clear()
    await callback.answer("Сохранено")
    await _show_raffle(bot, callback.message.chat.id, session, raffle, runtime_settings)
