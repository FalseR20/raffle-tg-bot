"""Payment method management with MarkdownV2 validation."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Organization
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import organizations as orgs_repo
from bot.db.repositories import raffles as raffles_repo
from bot.handlers.admin.organizations import _guard, _send_orgs
from bot.keyboards.admin import payment_method_card, payment_methods_menu, wizard_cancel
from bot.middlewares.admin import ManagersOnlyMiddleware
from bot.services import notifications
from bot.services.permissions import Permissions
from bot.states import PaymentMethodWizard
from bot.texts.markdown import MarkdownError, escape_markdown_v2, validate_markdown_v2
from bot.texts.telegram import markdown_from_entities
from bot.utils.time import utcnow

router = Router(name="admin_payment_methods")
router.callback_query.middleware(ManagersOnlyMiddleware())
router.message.middleware(ManagersOnlyMiddleware())


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
    text = markdown_from_entities(message.text or "", message.entities)
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
    text = markdown_from_entities(message.text or "", message.entities)
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
        await audit_repo.log(
            session,
            entity_type="payment_method",
            entity_id=method.id,
            actor_id=permissions.user_id,
            action="payment_method_updated",
            payload={"field": "sort_order"},
            at=utcnow(),
        )
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
