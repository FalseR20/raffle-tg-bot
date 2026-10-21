"""Organization and payment method management."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Organization
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import organizations as orgs_repo
from bot.db.repositories import raffles as raffles_repo
from bot.keyboards.admin import (
    organization_card,
    organizations_menu,
    payment_method_card,
    payment_methods_menu,
    wizard_cancel,
)
from bot.services import notifications
from bot.services.permissions import Permissions
from bot.states import OrganizationWizard, PaymentMethodWizard
from bot.texts.markdown import MarkdownError, escape_markdown_v2, validate_markdown_v2
from bot.utils.slug import slugify, unique_slug
from bot.utils.time import utcnow

router = Router(name="admin_organizations")


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
        "Введите короткое описание организации (обычный текст):",
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
        "Введите ID чата проверки (например, -1001234567890). "
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
        "Введите ID темы форума или «-», если тема не нужна:",
        reply_markup=wizard_cancel("adm:orgs"),
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
        await notifications.send_text(bot, message.chat.id, "Введите число или «-»\\.")
        return
    await state.update_data(review_thread_id=thread_id)
    await state.set_state(OrganizationWizard.contact_url)
    await notifications.send_text(
        bot,
        message.chat.id,
        "Введите контактную ссылку или «-»:",
        reply_markup=wizard_cancel("adm:orgs"),
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
    data = await state.get_data()
    organization = await orgs_repo.create(
        session,
        name=data["name"],
        slug=await _unique_slug(session, data["name"]),
        description=data.get("description", ""),
        review_chat_id=int(data["review_chat_id"]),
        review_thread_id=data.get("review_thread_id"),
        contact_url=None if raw == "-" else raw,
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
    await notifications.send_text(bot, message.chat.id, "Организация создана\\.")
    await _show_org(bot, message.chat.id, session, organization)


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
        "slug": "Введите новый slug (латиница, цифры, дефис):",
        "description": "Введите новое описание:",
        "chat": "Введите ID чата проверки:",
        "thread": "Введите ID темы или «-»:",
        "contact": "Введите контактную ссылку или «-»:",
    }
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        prompts.get(field, "Введите значение:"),
        reply_markup=wizard_cancel(f"adm:org:{organization.id}"),
    )


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


# ----------------------------- payment methods ------------------------------


async def _show_methods(
    bot: Bot, chat_id: int, session: AsyncSession, organization: Organization
) -> None:
    methods = await orgs_repo.load_payment_methods(session, organization.id)
    await notifications.send_text(
        bot,
        chat_id,
        f"💳 *{escape_markdown_v2(organization.name)}*\n\nСпособы оплаты:",
        reply_markup=payment_methods_menu(
            organization.id,
            [(method.id, method.title, method.is_active, method.sort_order) for method in methods],
        ),
    )


@router.callback_query(F.data.regexp(r"^adm:methods(:\d+)?$"))
async def on_methods(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None:
        return
    if not _guard(permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    if callback.data and ":" in callback.data:
        organization = await orgs_repo.get(session, int(callback.data.rsplit(":", 1)[1]))
    else:
        await callback.answer()
        await _send_orgs(bot, callback.message.chat.id, session)
        return
    if organization is None:
        await callback.answer("Организация не найдена", show_alert=True)
        return
    await callback.answer()
    await _show_methods(bot, callback.message.chat.id, session, organization)


@router.callback_query(F.data.regexp(r"^adm:method:\d+$"))
async def on_method(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None or callback.data is None:
        return
    if not _guard(permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    method = await orgs_repo.get_payment_method(session, int(callback.data.rsplit(":", 1)[1]))
    if method is None:
        await callback.answer("Способ оплаты не найден", show_alert=True)
        return
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        f"💳 *{escape_markdown_v2(method.title)}*\n\n"
        f"{method.instructions_text}\n\n"
        f"Статус: {'🟢 активен' if method.is_active else '⚪️ выключен'}",
        reply_markup=payment_method_card(method.organization_id, method.id, method.is_active),
    )


@router.callback_query(F.data.regexp(r"^adm:method_new:\d+$"))
async def on_method_new(
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
    organization_id = int(callback.data.rsplit(":", 1)[1])
    await state.set_state(PaymentMethodWizard.title)
    await state.update_data(organization_id=organization_id)
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "Введите название способа оплаты (до 64 символов), например «Карта BYN»:",
        reply_markup=wizard_cancel(f"adm:methods:{organization_id}"),
    )


@router.message(PaymentMethodWizard.title, F.text)
async def on_method_title(
    message: Message, bot: Bot, state: FSMContext, permissions: Permissions
) -> None:
    if not _guard(permissions):
        return
    title = (message.text or "").strip()
    if not title or len(title) > 64:
        await notifications.send_text(bot, message.chat.id, "Название: от 1 до 64 символов\\.")
        return
    await state.update_data(title=title)
    await state.set_state(PaymentMethodWizard.instructions)
    organization_id = await _org_id(state)
    await notifications.send_text(
        bot,
        message.chat.id,
        "Введите инструкцию в MarkdownV2\\.\n\n"
        "• копируемые значения — в обратных кавычках: `1234 5678`\n"
        "• жирный: *текст*\n"
        "• ссылка: [текст](https://example\\.com)\n"
        "• точку, тире, скобки и другие спецсимволы экранируйте: \\\\.",
        reply_markup=wizard_cancel(f"adm:methods:{organization_id}"),
    )


async def _org_id(state: FSMContext) -> int:
    data = await state.get_data()
    return int(data.get("organization_id", 0))


@router.message(PaymentMethodWizard.instructions, F.text)
async def on_method_instructions(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    permissions: Permissions,
    state: FSMContext,
) -> None:
    if not _guard(permissions):
        return
    text = message.text or ""
    try:
        _validate_instructions(text)
    except ValueError as exc:
        await notifications.send_text(bot, message.chat.id, escape_markdown_v2(str(exc)))
        return
    data = await state.get_data()
    organization = await orgs_repo.get(session, int(data["organization_id"]))
    if organization is None:
        await state.clear()
        return
    method = await orgs_repo.create_payment_method(
        session,
        organization_id=organization.id,
        title=data["title"],
        instructions_text=text,
        is_active=True,
        sort_order=await orgs_repo.next_method_sort_order(session, organization.id),
    )
    await audit_repo.log(
        session,
        entity_type="payment_method",
        entity_id=method.id,
        actor_id=permissions.user_id,
        action="payment_method_created",
        payload={"organization_id": organization.id, "title": method.title},
        at=utcnow(),
    )
    await session.commit()
    await state.clear()
    await notifications.send_text(bot, message.chat.id, "Способ оплаты создан\\.")
    await _show_methods(bot, message.chat.id, session, organization)


def _validate_instructions(text: str) -> None:
    if not text.strip():
        raise ValueError("Инструкция не может быть пустой.")
    if len(text) > 3000:
        raise ValueError("Инструкция не может быть длиннее 3000 символов.")
    try:
        validate_markdown_v2(text)
    except MarkdownError as exc:
        raise ValueError(
            f"Некорректный MarkdownV2: {exc}. Экранируйте спецсимволы обратным слэшем."
        ) from exc


@router.callback_query(F.data.regexp(r"^adm:method_set:\d+:(title|instructions)$"))
async def on_method_set(
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
    _, _, method_id_raw, field = callback.data.split(":")
    method = await orgs_repo.get_payment_method(session, int(method_id_raw))
    if method is None:
        await callback.answer("Способ оплаты не найден", show_alert=True)
        return
    await state.update_data(method_id=method.id)
    if field == "title":
        await state.set_state(PaymentMethodWizard.edit_title)
        prompt = "Введите новое название:"
    else:
        await state.set_state(PaymentMethodWizard.edit_instructions)
        prompt = "Введите новую инструкцию в MarkdownV2:"
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        prompt,
        reply_markup=wizard_cancel(f"adm:method:{method.id}"),
    )


@router.message(PaymentMethodWizard.edit_title, F.text)
async def on_method_edit_title(
    message: Message, bot: Bot, session: AsyncSession, permissions: Permissions, state: FSMContext
) -> None:
    if not _guard(permissions):
        return
    data = await state.get_data()
    method = await orgs_repo.get_payment_method(session, int(data.get("method_id", 0)))
    title = (message.text or "").strip()
    if method is None or not title or len(title) > 64:
        await notifications.send_text(bot, message.chat.id, "Название: от 1 до 64 символов\\.")
        return
    method.title = title
    await audit_repo.log(
        session,
        entity_type="payment_method",
        entity_id=method.id,
        actor_id=permissions.user_id,
        action="payment_method_updated",
        payload={"field": "title"},
        at=utcnow(),
    )
    await session.commit()
    await state.clear()
    await notifications.send_text(
        bot,
        message.chat.id,
        "Сохранено\\.",
        reply_markup=payment_method_card(method.organization_id, method.id, method.is_active),
    )


@router.message(PaymentMethodWizard.edit_instructions, F.text)
async def on_method_edit_instructions(
    message: Message, bot: Bot, session: AsyncSession, permissions: Permissions, state: FSMContext
) -> None:
    if not _guard(permissions):
        return
    data = await state.get_data()
    method = await orgs_repo.get_payment_method(session, int(data.get("method_id", 0)))
    if method is None:
        await state.clear()
        return
    text = message.text or ""
    try:
        _validate_instructions(text)
    except ValueError as exc:
        await notifications.send_text(bot, message.chat.id, escape_markdown_v2(str(exc)))
        return
    method.instructions_text = text
    await audit_repo.log(
        session,
        entity_type="payment_method",
        entity_id=method.id,
        actor_id=permissions.user_id,
        action="payment_method_updated",
        payload={"field": "instructions"},
        at=utcnow(),
    )
    await session.commit()
    await state.clear()
    await notifications.send_text(
        bot,
        message.chat.id,
        "Сохранено\\.",
        reply_markup=payment_method_card(method.organization_id, method.id, method.is_active),
    )


@router.callback_query(F.data.regexp(r"^adm:method_toggle:\d+$"))
async def on_method_toggle(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None or callback.data is None:
        return
    if not _guard(permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    method = await orgs_repo.get_payment_method(session, int(callback.data.rsplit(":", 1)[1]))
    if method is None:
        await callback.answer("Способ оплаты не найден", show_alert=True)
        return
    if method.is_active:
        current = await raffles_repo.get_current(session)
        if current is not None and await raffles_repo.is_organization_enabled(
            session, current.id, method.organization_id
        ):
            active_methods = await orgs_repo.load_payment_methods(
                session, method.organization_id, only_active=True
            )
            if len(active_methods) <= 1:
                await callback.answer(
                    "Нельзя выключить последний активный способ оплаты организации "
                    "в текущем розыгрыше.",
                    show_alert=True,
                )
                return
    method.is_active = not method.is_active
    await audit_repo.log(
        session,
        entity_type="payment_method",
        entity_id=method.id,
        actor_id=permissions.user_id,
        action="payment_method_updated",
        payload={"is_active": method.is_active},
        at=utcnow(),
    )
    await session.commit()
    await callback.answer("Статус изменён")
    organization = await orgs_repo.get(session, method.organization_id)
    if callback.message and organization is not None:
        await _show_methods(bot, callback.message.chat.id, session, organization)


@router.callback_query(F.data.regexp(r"^adm:method_preview:\d+$"))
async def on_method_preview(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None or callback.data is None:
        return
    if not _guard(permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    method = await orgs_repo.get_payment_method(session, int(callback.data.rsplit(":", 1)[1]))
    if method is None:
        await callback.answer("Способ оплаты не найден", show_alert=True)
        return
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        f"👁 *{escape_markdown_v2(method.title)}*\n\n{method.instructions_text}",
    )


@router.callback_query(F.data.regexp(r"^adm:method_up:\d+$"))
async def on_method_up(
    callback: CallbackQuery, bot: Bot, session: AsyncSession, permissions: Permissions
) -> None:
    if callback.message is None or callback.data is None:
        return
    if not _guard(permissions):
        await callback.answer("Нет доступа", show_alert=True)
        return
    method = await orgs_repo.get_payment_method(session, int(callback.data.rsplit(":", 1)[1]))
    if method is None:
        await callback.answer("Способ не найден", show_alert=True)
        return
    methods = await orgs_repo.load_payment_methods(session, method.organization_id)
    index = next((i for i, item in enumerate(methods) if item.id == method.id), 0)
    if index > 0:
        previous = methods[index - 1]
        method.sort_order, previous.sort_order = previous.sort_order, method.sort_order
        await session.commit()
    await callback.answer("Порядок обновлён")
    organization = await orgs_repo.get(session, method.organization_id)
    if callback.message and organization is not None:
        await _show_methods(bot, callback.message.chat.id, session, organization)


@router.callback_query(F.data == "adm:markup_help")
async def on_markup_help(callback: CallbackQuery, bot: Bot) -> None:
    if callback.message is None:
        return
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "Форматирование MarkdownV2:\n"
        "`значение` — код и копирование\n"
        "*жирный* · _курсив_ · __подчёркнутый__ · ~зачёркнутый~\n"
        "[текст](https://example\\.com) — ссылка\n\n"
        "Спецсимволы \\. \\- \\! \\( \\) экранируются обратным слэшем\\.",
    )
