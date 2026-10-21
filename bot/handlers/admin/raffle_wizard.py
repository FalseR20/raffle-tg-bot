"""Raffle creation and editing wizards."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import RaffleStatus
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import organizations as orgs_repo
from bot.db.repositories import raffles as raffles_repo
from bot.handlers.admin.common import parse_datetime, parse_decimal
from bot.handlers.admin.raffles import (
    _get_raffle,
    _guard,
    _send_raffles,
    _show_raffle,
)
from bot.keyboards.admin import (
    edit_organizations,
    raffle_edit_fields,
    wizard_input,
    wizard_organizations,
)
from bot.middlewares.admin import ManagersOnlyMiddleware
from bot.services import notifications
from bot.services.errors import ServiceError
from bot.services.permissions import Permissions
from bot.services.raffles import (
    RaffleInput,
    create_raffle,
    extend_end_at,
    update_raffle,
    validate_input,
)
from bot.states import RaffleEdit, RaffleWizard
from bot.texts.markdown import escape_markdown_v2
from bot.utils.time import utcnow

router = Router(name="admin_raffle_wizard")
router.callback_query.middleware(ManagersOnlyMiddleware())
router.message.middleware(ManagersOnlyMiddleware())


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
    "description": "Введите описание и правила \\(обычный текст\\):",
    "ticket_price": "Введите цену билета в BYN, например 5,00:",
    "min_amount": "Введите минимальную сумму одной заявки или нажмите «Равна цене билета»:",
    "prizes_count": "Введите количество призов \\(целое число\\):",
    "start_at": "Введите дату и время начала: ДД\\.ММ\\.ГГГГ ЧЧ:ММ",
    "end_at": "Введите дату и время окончания: ДД\\.ММ\\.ГГГГ ЧЧ:ММ",
    "channel_url": "Введите ссылку на канал или нажмите «Взять из настроек»:",
    "channel_title": "Введите название канала или нажмите «Взять из настроек»:",
}


async def _prompt(
    bot: Bot,
    chat_id: int,
    step: str,
    state: FSMContext,
    *,
    default_callback: str | None = None,
    default_text: str = "✅ По умолчанию",
) -> None:
    await state.set_state(getattr(RaffleWizard, step))
    await notifications.send_text(
        bot,
        chat_id,
        _PROMPTS[step],
        reply_markup=wizard_input(
            "rw:cancel",
            default_callback=default_callback,
            default_text=default_text,
        ),
    )


async def _start_organization_selection(
    bot: Bot,
    session: AsyncSession,
    *,
    chat_id: int,
    state: FSMContext,
    data: dict,
) -> None:
    organizations = await orgs_repo.list_all(session, only_active=True)
    selected = [organization.id for organization in organizations]
    await state.update_data(
        channel_title=data["channel_title"],
        selected_organizations=selected,
    )
    await state.set_state(RaffleWizard.organizations)
    await notifications.send_text(
        bot,
        chat_id,
        "Выберите организации для розыгрыша:",
        reply_markup=wizard_organizations(
            [(organization.id, organization.name) for organization in organizations],
            set(selected),
        ),
    )


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
        await _prompt(
            bot,
            message.chat.id,
            "min_amount",
            state,
            default_callback="rw:default:min_amount",
            default_text="✅ Равна цене билета",
        )
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
        await _prompt(
            bot,
            message.chat.id,
            "channel_url",
            state,
            default_callback="rw:default:channel_url",
            default_text="✅ Взять из настроек",
        )
        return
    if step == "channel_url":
        data["channel_url"] = runtime_settings.channel_url or "" if raw == "-" else raw
        await state.update_data(**data)
        await _prompt(
            bot,
            message.chat.id,
            "channel_title",
            state,
            default_callback="rw:default:channel_title",
            default_text="✅ Взять из настроек",
        )
        return
    if step == "channel_title":
        data["channel_title"] = runtime_settings.channel_title or "" if raw == "-" else raw
        await _start_organization_selection(
            bot, session, chat_id=message.chat.id, state=state, data=data
        )
        return


async def _is_in_state(state: FSMContext, expected) -> bool:
    return await state.get_state() == expected.state


@router.callback_query(F.data == "rw:default:min_amount")
async def on_default_min_amount(
    callback: CallbackQuery,
    bot: Bot,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    if callback.message is None:
        return
    if not _guard(callback, permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    if not await _is_in_state(state, RaffleWizard.min_amount):
        await callback.answer("Шаг уже неактуален", show_alert=True)
        return
    data = await state.get_data()
    data["min_amount"] = data["ticket_price"]
    await state.update_data(**data)
    await callback.answer("Минимальная сумма = цена билета")
    await _prompt(bot, callback.message.chat.id, "prizes_count", state)


@router.callback_query(F.data == "rw:default:channel_url")
async def on_default_channel_url(
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
    if not await _is_in_state(state, RaffleWizard.channel_url):
        await callback.answer("Шаг уже неактуален", show_alert=True)
        return
    data = await state.get_data()
    data["channel_url"] = runtime_settings.channel_url or ""
    await state.update_data(**data)
    await callback.answer("Взято из настроек")
    await _prompt(
        bot,
        callback.message.chat.id,
        "channel_title",
        state,
        default_callback="rw:default:channel_title",
        default_text="✅ Взять из настроек",
    )


@router.callback_query(F.data == "rw:default:channel_title")
async def on_default_channel_title(
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
    if not await _is_in_state(state, RaffleWizard.channel_title):
        await callback.answer("Шаг уже неактуален", show_alert=True)
        return
    data = await state.get_data()
    data["channel_title"] = runtime_settings.channel_title or ""
    await callback.answer("Взято из настроек")
    await _start_organization_selection(
        bot, session, chat_id=callback.message.chat.id, state=state, data=data
    )


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
        "end_at": "Введите новую дату окончания: ДД\\.ММ\\.ГГГГ ЧЧ:ММ",
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
    if raffle.status is not RaffleStatus.DRAFT:
        await callback.answer("Организации можно менять только в черновике", show_alert=True)
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
