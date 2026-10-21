"""Exports and print-ticket PDF from the admin panel."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.repositories import audit as audit_repo
from bot.db.repositories import raffles as raffles_repo
from bot.keyboards.admin import export_formats, exported_raffles
from bot.services import notifications
from bot.services.errors import ExportError, ServiceError
from bot.services.exports import ExportRequest, build_export, build_tickets_pdf
from bot.services.permissions import Permissions
from bot.services.settings import RuntimeSettings
from bot.texts.markdown import escape_markdown_v2
from bot.utils.time import utcnow

router = Router(name="admin_exports")


async def _list_raffles(callback: CallbackQuery, bot: Bot, session: AsyncSession) -> None:
    if callback.message is None:
        return
    raffles = await raffles_repo.list_all(session, limit=30)
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "📤 *Экспорт*\n\nВыберите розыгрыш:",
        reply_markup=exported_raffles([(raffle.id, raffle.title) for raffle in raffles]),
    )


@router.callback_query(F.data == "adm:exports")
async def on_exports(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None:
        return
    if not permissions.has_admin_panel:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await _list_raffles(callback, bot, session)


@router.callback_query(F.data.regexp(r"^adm:export_raffle:\d+$"))
async def on_export_raffle(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if callback.message is None or callback.data is None:
        return
    raffle = await raffles_repo.get(session, int(callback.data.rsplit(":", 1)[1]))
    if raffle is None:
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return
    data = await state.get_data()
    include_all = bool(data.get("include_all", False))
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        f"📤 *{escape_markdown_v2(raffle.title)}*\n\n"
        "Выберите формат\\. Файлы содержат персональные данные — не публикуйте их в чатах\\.",
        reply_markup=export_formats(
            raffle.id,
            can_pdf=raffle.status.value in {"finished", "archived"},
            include_all=include_all,
        ),
    )


@router.callback_query(F.data.regexp(r"^adm:export_toggle:\d+$"))
async def on_export_toggle(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if callback.message is None or callback.data is None:
        return
    raffle = await raffles_repo.get(session, int(callback.data.rsplit(":", 1)[1]))
    if raffle is None:
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return
    data = await state.get_data()
    include_all = not bool(data.get("include_all", False))
    await state.update_data(include_all=include_all)
    await callback.answer("Все статусы" if include_all else "Только подтверждённые")
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        (
            "Будут включены все статусы\\. Выберите формат:"
            if include_all
            else "Будут включены только подтверждённые заявки\\. Выберите формат:"
        ),
        reply_markup=export_formats(
            raffle.id,
            can_pdf=raffle.status.value in {"finished", "archived"},
            include_all=include_all,
        ),
    )


@router.callback_query(F.data.regexp(r"^adm:export_do:\d+:(csv|xlsx|pdf)$"))
async def on_export_do(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    runtime_settings: RuntimeSettings,
    state: FSMContext,
) -> None:
    if callback.message is None or callback.data is None:
        return
    _, _, raffle_raw, format_name = callback.data.split(":")
    raffle = await raffles_repo.get(session, int(raffle_raw))
    if raffle is None:
        await callback.answer("Розыгрыш не найден", show_alert=True)
        return
    if not permissions.has_admin_panel:
        await callback.answer("Нет доступа", show_alert=True)
        return
    if format_name == "pdf" and not permissions.can_manage_raffles:
        await callback.answer("PDF билетов доступен только организатору", show_alert=True)
        return

    try:
        if format_name == "pdf":
            export = build_tickets_pdf(raffle)
        else:
            include_all = bool((await state.get_data()).get("include_all", False))
            export = await build_export(
                session,
                raffle=raffle,
                format_name=format_name,
                request=ExportRequest(
                    include_all_statuses=include_all,
                    allowed_organization_ids=permissions.allowed_organization_ids(),
                    is_org_rep=permissions.is_org_rep and not permissions.can_manage_raffles,
                ),
                tz=runtime_settings.timezone,
            )
    except (ExportError, ServiceError) as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return

    if len(export.data) > 45 * 1024 * 1024:
        await callback.answer("Файл больше 45 МБ, экспорт остановлен", show_alert=True)
        return
    await callback.answer("Файл готовится")
    caption = "📤 " + export.filename + "\n\n⚠️ Файл содержит персональные данные\\."
    await bot.send_document(
        callback.message.chat.id,
        BufferedInputFile(export.data, filename=export.filename),
        caption=caption,
        parse_mode="MarkdownV2",
    )
    await audit_repo.log(
        session,
        entity_type="export",
        entity_id=export.filename,
        raffle_id=raffle.id,
        actor_id=permissions.user_id,
        action="export_created",
        payload={
            "format": format_name,
            "filename": export.filename,
            "scope": sorted(permissions.allowed_organization_ids() or []),
        },
        at=utcnow(),
    )
    await session.commit()


@router.callback_query(F.data == "adm:export_pdf_locked")
async def on_pdf_locked(callback: CallbackQuery) -> None:
    await callback.answer("PDF доступен только после завершения розыгрыша", show_alert=True)


@router.callback_query(F.data.regexp(r"^adm:pdf:\d+$"))
async def on_pdf_shortcut(
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
    try:
        export = build_tickets_pdf(raffle)
    except ExportError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer()
    await bot.send_document(
        callback.message.chat.id,
        BufferedInputFile(export.data, filename=export.filename),
        caption="🖨 PDF билетов\\. Билеты без персональных данных\\.",
        parse_mode="MarkdownV2",
    )
