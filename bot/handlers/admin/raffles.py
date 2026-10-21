"""Raffle management in the admin panel."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ApplicationStatus, RaffleStatus
from bot.db.models import Raffle
from bot.db.repositories import applications as apps_repo
from bot.db.repositories import participants as participants_repo
from bot.db.repositories import raffles as raffles_repo
from bot.handlers.admin.common import parse_datetime
from bot.keyboards.admin import (
    confirm_action,
    finish_confirmation,
    raffle_card,
    raffles_list,
    wizard_cancel,
)
from bot.middlewares.admin import ManagersOnlyMiddleware
from bot.services import notifications
from bot.services.errors import ServiceError
from bot.services.permissions import Permissions
from bot.services.raffles import (
    activate,
    archive,
    delete_draft,
    duplicate,
    extend_end_at,
    finish,
    resume,
    return_to_draft,
    stop_acceptance,
)
from bot.states import RaffleWizard
from bot.texts import ru
from bot.texts.markdown import escape_markdown_v2
from bot.utils.time import datetime_input_example, format_datetime, utcnow

router = Router(name="admin_raffles")
router.callback_query.middleware(ManagersOnlyMiddleware())
router.message.middleware(ManagersOnlyMiddleware())


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
    # Re-load after commit so relationships such as organizations are populated
    # inside the async session, never through an implicit lazy load.
    refreshed = await raffles_repo.get(session, raffle.id)
    if refreshed is not None:
        raffle = refreshed
    text = await _raffle_card_text(session, raffle, settings)
    applications_count = await raffles_repo.count_applications(session, raffle.id)
    await notifications.send_text(
        bot,
        chat_id,
        text,
        reply_markup=raffle_card(
            raffle.id,
            raffle.status,
            can_manage=True,
            applications_count=applications_count,
        ),
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
    if raffle.status is RaffleStatus.ACTIVE:
        await callback.answer()
        await notifications.send_text(
            bot,
            callback.message.chat.id,
            "Сначала остановите приём заявок, затем завершите розыгрыш\\.",
            reply_markup=confirm_action(
                "⏹ Остановить приём",
                f"adm:raffle_stop_yes:{raffle.id}",
                f"adm:raffle:{raffle.id}",
            ),
        )
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


@router.callback_query(F.data.regexp(r"^adm:raffle_draft:\d+$"))
async def on_return_to_draft(
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
        await return_to_draft(session, actor=permissions, raffle=raffle, now=utcnow())
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer("Возвращено в черновик")
    await _show_raffle(bot, callback.message.chat.id, session, raffle, runtime_settings)


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
    example = escape_markdown_v2(datetime_input_example(utcnow(), runtime_settings.timezone))
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        f"Введите новую дату окончания в формате ДД\\.ММ\\.ГГГГ ЧЧ:ММ\\. Например: {example}\\.",
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
