"""Organization and payment method management."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Organization
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import organizations as orgs_repo
from bot.keyboards.admin import (
    organization_card,
    organizations_menu,
    wizard_cancel,
    wizard_input,
)
from bot.middlewares.admin import ManagersOnlyMiddleware
from bot.services import notifications
from bot.services.permissions import Permissions
from bot.states import OrganizationWizard
from bot.texts.markdown import escape_markdown_v2
from bot.utils.slug import slugify, unique_slug
from bot.utils.time import utcnow

router = Router(name="admin_organizations")
router.callback_query.middleware(ManagersOnlyMiddleware())
router.message.middleware(ManagersOnlyMiddleware())


def _guard(permissions: Permissions) -> bool:
    return permissions.can_manage_organizations


async def _send_orgs(bot: Bot, chat_id: int, session: AsyncSession) -> None:
    organizations = await orgs_repo.list_all(session)
    await notifications.send_text(
        bot,
        chat_id,
        "🏢 *Организации*",
        reply_markup=organizations_menu(
            [(org.id, org.name, org.is_active) for org in organizations]
        ),
    )


async def _org_card_text(session: AsyncSession, organization: Organization) -> str:
    methods = await orgs_repo.load_payment_methods(session, organization.id)
    count = await orgs_repo.count_applications(session, organization.id)
    thread = organization.review_thread_id if organization.review_thread_id is not None else "—"
    return (
        f"🏢 *{escape_markdown_v2(organization.name)}*\n\n"
        f"Slug: `{escape_markdown_v2(organization.slug)}`\n"
        f"Описание: {escape_markdown_v2(organization.description or '—')}\n"
        f"Чат проверки: `{organization.review_chat_id}`\n"
        f"Тема: `{thread}`\n"
        f"Контакт: {escape_markdown_v2(organization.contact_url or '—')}\n"
        f"Статус: {'🟢 активна' if organization.is_active else '⚪️ выключена'}\n"
        f"Способов оплаты: {len(methods)} · Заявок: {count}"
    )


async def _show_org(
    bot: Bot, chat_id: int, session: AsyncSession, organization: Organization
) -> None:
    await notifications.send_text(
        bot,
        chat_id,
        await _org_card_text(session, organization),
        reply_markup=organization_card(organization.id, organization.is_active),
    )


async def _unique_slug(session: AsyncSession, name: str, exclude_id: int | None = None) -> str:
    existing = await orgs_repo.list_all(session)
    taken = {organization.slug for organization in existing if organization.id != exclude_id}
    return unique_slug(slugify(name), taken)


@router.callback_query(F.data == "adm:orgs")
async def on_orgs(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None:
        return
    if not _guard(permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.answer()
    await _send_orgs(bot, callback.message.chat.id, session)


@router.callback_query(F.data.regexp(r"^adm:org:\d+$"))
async def on_org(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None or callback.data is None:
        return
    if not _guard(permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    organization = await orgs_repo.get(session, int(callback.data.rsplit(":", 1)[1]))
    if organization is None:
        await callback.answer("Организация не найдена", show_alert=True)
        return
    await callback.answer()
    await _show_org(bot, callback.message.chat.id, session, organization)


@router.callback_query(F.data == "adm:org_new")
async def on_org_new(
    callback: CallbackQuery,
    bot: Bot,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    if callback.message is None:
        return
    if not _guard(permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(OrganizationWizard.name)
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "Введите название организации:",
        reply_markup=wizard_cancel("adm:orgs"),
    )


@router.message(OrganizationWizard.name, F.text)
async def on_org_name(
    message: Message, bot: Bot, session: AsyncSession, state: FSMContext, permissions: Permissions
) -> None:
    if not _guard(permissions):
        return
    name = (message.text or "").strip()
    if not name:
        await notifications.send_text(bot, message.chat.id, "Название не может быть пустым\\.")
        return
    await state.update_data(name=name)
    await state.set_state(OrganizationWizard.description)
    await notifications.send_text(
        bot,
        message.chat.id,
        "Введите короткое описание организации \\(обычный текст\\):",
        reply_markup=wizard_cancel("adm:orgs"),
    )


@router.message(OrganizationWizard.description, F.text)
async def on_org_description(
    message: Message, bot: Bot, state: FSMContext, permissions: Permissions
) -> None:
    if not _guard(permissions):
        return
    await state.update_data(description=(message.text or "").strip())
    await state.set_state(OrganizationWizard.review_chat)
    await notifications.send_text(
        bot,
        message.chat.id,
        "Введите ID чата проверки \\(например, \\-1001234567890\\)\\. "
        "Команда /chatid покажет ID текущего чата\\.",
        reply_markup=wizard_cancel("adm:orgs"),
    )


@router.message(OrganizationWizard.review_chat, F.text)
async def on_org_chat(
    message: Message, bot: Bot, state: FSMContext, permissions: Permissions
) -> None:
    if not _guard(permissions):
        return
    try:
        chat_id = int((message.text or "").strip())
    except ValueError:
        await notifications.send_text(bot, message.chat.id, "ID чата должен быть числом\\.")
        return
    await state.update_data(review_chat_id=chat_id)
    await state.set_state(OrganizationWizard.review_thread)
    await notifications.send_text(
        bot,
        message.chat.id,
        "Введите ID темы форума или нажмите «Без темы»:",
        reply_markup=wizard_input(
            "adm:orgs",
            default_callback="orgwiz:default:thread",
            default_text="✅ Без темы",
        ),
    )


@router.message(OrganizationWizard.review_thread, F.text)
async def on_org_thread(
    message: Message, bot: Bot, state: FSMContext, permissions: Permissions
) -> None:
    if not _guard(permissions):
        return
    raw = (message.text or "").strip()
    try:
        thread_id = None if raw == "-" else int(raw)
    except ValueError:
        await notifications.send_text(
            bot, message.chat.id, "Введите число или нажмите «Без темы»\\."
        )
        return
    await state.update_data(review_thread_id=thread_id)
    await _prompt_contact(bot, message.chat.id, state)


async def _prompt_contact(bot: Bot, chat_id: int, state: FSMContext) -> None:
    await state.set_state(OrganizationWizard.contact_url)
    await notifications.send_text(
        bot,
        chat_id,
        "Введите контактную ссылку или нажмите «Без контакта»:",
        reply_markup=wizard_input(
            "adm:orgs",
            default_callback="orgwiz:default:contact",
            default_text="✅ Без контакта",
        ),
    )


@router.callback_query(F.data == "orgwiz:default:thread")
async def on_org_default_thread(
    callback: CallbackQuery,
    bot: Bot,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    if callback.message is None:
        return
    if not _guard(permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    if await state.get_state() != OrganizationWizard.review_thread.state:
        await callback.answer("Шаг уже неактуален", show_alert=True)
        return
    await state.update_data(review_thread_id=None)
    await callback.answer("Без темы")
    await _prompt_contact(bot, callback.message.chat.id, state)


@router.callback_query(F.data == "orgwiz:default:contact")
async def on_org_default_contact(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    if callback.message is None:
        return
    if not _guard(permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    if await state.get_state() != OrganizationWizard.contact_url.state:
        await callback.answer("Шаг уже неактуален", show_alert=True)
        return
    await callback.answer("Без контакта")
    await _create_organization(
        bot,
        session,
        state=state,
        permissions=permissions,
        chat_id=callback.message.chat.id,
        contact_url=None,
    )


@router.message(OrganizationWizard.contact_url, F.text)
async def on_org_contact(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    state: FSMContext,
    permissions: Permissions,
) -> None:
    if not _guard(permissions):
        return
    raw = (message.text or "").strip()
    await _create_organization(
        bot,
        session,
        state=state,
        permissions=permissions,
        chat_id=message.chat.id,
        contact_url=None if raw == "-" else raw,
    )


async def _create_organization(
    bot: Bot,
    session: AsyncSession,
    *,
    state: FSMContext,
    permissions: Permissions,
    chat_id: int,
    contact_url: str | None,
) -> None:
    data = await state.get_data()
    if not data.get("name") or not data.get("review_chat_id"):
        await state.clear()
        return
    organization = await orgs_repo.create(
        session,
        name=data["name"],
        slug=await _unique_slug(session, data["name"]),
        description=data.get("description", ""),
        review_chat_id=int(data["review_chat_id"]),
        review_thread_id=data.get("review_thread_id"),
        contact_url=contact_url,
        is_active=True,
    )
    await audit_repo.log(
        session,
        entity_type="organization",
        entity_id=organization.id,
        actor_id=permissions.user_id,
        action="organization_created",
        payload={"name": organization.name},
        at=utcnow(),
    )
    await session.commit()
    await state.clear()
    await notifications.send_text(bot, chat_id, "Организация создана\\.")
    await _show_org(bot, chat_id, session, organization)


@router.callback_query(F.data.regexp(r"^adm:org_toggle:\d+$"))
async def on_org_toggle(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None or callback.data is None:
        return
    if not _guard(permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    organization = await orgs_repo.get(session, int(callback.data.rsplit(":", 1)[1]))
    if organization is None:
        await callback.answer("Организация не найдена", show_alert=True)
        return
    organization.is_active = not organization.is_active
    await audit_repo.log(
        session,
        entity_type="organization",
        entity_id=organization.id,
        actor_id=permissions.user_id,
        action="organization_updated",
        payload={"is_active": organization.is_active},
        at=utcnow(),
    )
    await session.commit()
    await callback.answer("Статус изменён")
    await _show_org(bot, callback.message.chat.id, session, organization)


@router.callback_query(F.data.regexp(r"^adm:org_set:\d+:\w+$"))
async def on_org_set(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    if callback.message is None or callback.data is None:
        return
    if not _guard(permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, organization_id_raw, field = callback.data.split(":")
    organization = await orgs_repo.get(session, int(organization_id_raw))
    if organization is None:
        await callback.answer("Организация не найдена", show_alert=True)
        return
    await state.set_state(OrganizationWizard.edit_value)
    await state.update_data(organization_id=organization.id, field=field)
    await callback.answer()
    prompts = {
        "name": "Введите новое название:",
        "slug": "Введите новый slug \\(латиница, цифры, дефис\\):",
        "description": "Введите новое описание:",
        "chat": "Введите ID чата проверки:",
        "thread": "Введите ID темы или нажмите «Без темы»:",
        "contact": "Введите контактную ссылку или нажмите «Без контакта»:",
    }
    default_text = None
    default_callback = None
    if field == "thread":
        default_text = "✅ Без темы"
        default_callback = "orgwiz:edit_default"
    elif field == "contact":
        default_text = "✅ Без контакта"
        default_callback = "orgwiz:edit_default"
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        prompts.get(field, "Введите значение:"),
        reply_markup=wizard_input(
            f"adm:org:{organization.id}",
            default_callback=default_callback,
            default_text=default_text or "✅ По умолчанию",
        ),
    )


@router.callback_query(F.data == "orgwiz:edit_default")
async def on_org_edit_default(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    if callback.message is None:
        return
    if not _guard(permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    if await state.get_state() != OrganizationWizard.edit_value.state:
        await callback.answer("Шаг уже неактуален", show_alert=True)
        return
    data = await state.get_data()
    field = data.get("field")
    if field not in {"thread", "contact"}:
        await callback.answer("Для этого поля нет значения по умолчанию", show_alert=True)
        return
    organization = await orgs_repo.get(session, int(data.get("organization_id", 0)))
    if organization is None:
        await state.clear()
        await callback.answer("Организация не найдена", show_alert=True)
        return
    if field == "thread":
        organization.review_thread_id = None
    else:
        organization.contact_url = None
    await audit_repo.log(
        session,
        entity_type="organization",
        entity_id=organization.id,
        actor_id=permissions.user_id,
        action="organization_updated",
        payload={"field": field, "value": None},
        at=utcnow(),
    )
    await session.commit()
    await state.clear()
    await callback.answer("Очищено")
    await _show_org(bot, callback.message.chat.id, session, organization)


@router.message(OrganizationWizard.edit_value, F.text)
async def on_org_edit_value(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    if not _guard(permissions):
        return
    data = await state.get_data()
    organization = await orgs_repo.get(session, int(data.get("organization_id", 0)))
    if organization is None:
        await state.clear()
        return
    raw = (message.text or "").strip()
    field = data.get("field")
    try:
        if field == "name":
            if not raw:
                raise ValueError("Название не может быть пустым.")
            organization.name = raw
        elif field == "slug":
            new_slug = slugify(raw)
            if new_slug != raw:
                raise ValueError("Slug может содержать только латиницу, цифры и дефис.")
            for other in await orgs_repo.list_all(session):
                if other.id != organization.id and other.slug == new_slug:
                    raise ValueError("Такой slug уже занят.")
            organization.slug = new_slug
        elif field == "description":
            organization.description = raw
        elif field == "chat":
            organization.review_chat_id = int(raw)
        elif field == "thread":
            organization.review_thread_id = None if raw == "-" else int(raw)
        elif field == "contact":
            organization.contact_url = None if raw == "-" else raw
        else:
            raise ValueError("Неизвестное поле.")
    except ValueError as exc:
        await notifications.send_text(bot, message.chat.id, escape_markdown_v2(str(exc)))
        return
    await audit_repo.log(
        session,
        entity_type="organization",
        entity_id=organization.id,
        actor_id=permissions.user_id,
        action="organization_updated",
        payload={"field": field},
        at=utcnow(),
    )
    await session.commit()
    await state.clear()
    await _show_org(bot, message.chat.id, session, organization)


@router.callback_query(F.data.regexp(r"^adm:org_test:\d+$"))
async def on_org_test(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None or callback.data is None:
        return
    if not _guard(permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    organization = await orgs_repo.get(session, int(callback.data.rsplit(":", 1)[1]))
    if organization is None:
        await callback.answer("Организация не найдена", show_alert=True)
        return
    delivery = await notifications.send_text(
        bot,
        organization.review_chat_id,
        "🧪 Проверка связи\\. Бот сможет отправлять сюда карточки заявок\\.",
        message_thread_id=organization.review_thread_id,
    )
    if delivery.ok:
        await callback.answer("Сообщение отправлено")
    else:
        await callback.answer(f"Не удалось отправить: {delivery.error}"[:180], show_alert=True)
