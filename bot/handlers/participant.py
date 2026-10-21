"""Participant donation flow: organization, method, amount, receipt."""

from __future__ import annotations

from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import RaffleStatus
from bot.db.models import Application, Raffle, User
from bot.db.repositories import applications as apps_repo
from bot.db.repositories import organizations as orgs_repo
from bot.db.repositories import raffles as raffles_repo
from bot.domain.money import AmountError, format_money, parse_amount
from bot.domain.ordering import randomized_organizations
from bot.domain.tickets import suggest_amount
from bot.handlers.views import current_raffle, send_closed, send_no_raffle
from bot.keyboards.participant import (
    draft_keyboard,
    instructions_keyboard,
    organizations_keyboard,
    payment_methods_keyboard,
    receipt_keyboard,
)
from bot.services import applications as applications_service
from bot.services import notifications
from bot.services.errors import RaffleNotAcceptingError, ServiceError
from bot.services.ratelimit import RateLimiter
from bot.services.settings import RuntimeSettings
from bot.services.users import has_valid_consent
from bot.states import ParticipantFlow
from bot.texts import ru
from bot.texts.markdown import escape_markdown_v2

router = Router(name="participant")


async def _assert_consent(bot: Bot, chat_id: int, user: User, settings: RuntimeSettings) -> bool:
    if has_valid_consent(user, settings):
        return True
    await notifications.send_text(
        bot,
        chat_id,
        "Сначала примите правила и политику конфиденциальности: /start",
    )
    return False


async def _show_organizations(
    session: AsyncSession,
    bot: Bot,
    *,
    user: User,
    settings: RuntimeSettings,
    chat_id: int,
    now: datetime,
) -> None:
    raffle = await current_raffle(session, now)
    if raffle is None:
        await send_no_raffle(bot, chat_id, settings)
        return
    if raffle.status is not RaffleStatus.ACTIVE:
        await send_closed(bot, chat_id, settings)
        return
    if not await _assert_consent(bot, chat_id, user, settings):
        return
    organizations = await raffles_repo.enabled_organizations(session, raffle.id)
    organizations = [
        organization
        for organization in organizations
        if await orgs_repo.has_active_payment_methods(session, organization.id)
    ]
    organizations = randomized_organizations(organizations, user_id=user.id, raffle_id=raffle.id)
    await notifications.send_text(
        bot,
        chat_id,
        ru.active_raffle_text(raffle, settings.timezone, has_consent=True),
        reply_markup=organizations_keyboard(
            [(organization.id, organization.name) for organization in organizations]
        ),
    )


async def _show_instructions(
    session: AsyncSession,
    bot: Bot,
    *,
    application: Application,
    raffle: Raffle,
    settings: RuntimeSettings,
    chat_id: int,
    state: FSMContext,
) -> None:
    method = application.payment_method_title or "—"
    instructions = application.payment_instructions_snapshot or ""
    text = ru.payment_instructions_text(
        organization_name=application.organization.name,
        payment_method_title=method,
        payment_instructions=instructions,
        payment_code=application.payment_code,
        min_amount=raffle.min_amount,
        ticket_price=raffle.ticket_price,
    )
    await state.set_state(ParticipantFlow.amount)
    await state.update_data(application_id=application.id)
    await notifications.send_text(bot, chat_id, text, reply_markup=instructions_keyboard())


async def _continue_application(
    session: AsyncSession,
    bot: Bot,
    *,
    application: Application,
    raffle: Raffle,
    settings: RuntimeSettings,
    chat_id: int,
    state: FSMContext,
    now: datetime,
) -> None:
    methods = await orgs_repo.load_payment_methods(
        session, application.organization_id, only_active=True
    )
    if not methods:
        await notifications.send_text(
            bot,
            chat_id,
            "❌ У организации сейчас нет доступных способов оплаты\\. Выберите другую организацию:",
            reply_markup=instructions_keyboard(),
        )
        return

    if application.payment_method_id is None:
        if len(methods) == 1:
            await applications_service.choose_payment_method(
                session, application=application, method=methods[0], now=now
            )
            await session.commit()
            await _show_instructions(
                session,
                bot,
                application=application,
                raffle=raffle,
                settings=settings,
                chat_id=chat_id,
                state=state,
            )
            return
        await notifications.send_text(
            bot,
            chat_id,
            ru.payment_methods_text(application.organization.name),
            reply_markup=payment_methods_keyboard(
                [(method.id, method.title) for method in methods]
            ),
        )
        return

    await _show_instructions(
        session,
        bot,
        application=application,
        raffle=raffle,
        settings=settings,
        chat_id=chat_id,
        state=state,
    )


@router.callback_query(F.data.startswith("org:"))
async def on_organization_selected(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    runtime_settings: RuntimeSettings,
    state: FSMContext,
    now: datetime,
) -> None:
    organization_id = int(callback.data.split(":", 1)[1])
    raffle = await current_raffle(session, now)
    if callback.message is None:
        return
    if raffle is None or raffle.status is not RaffleStatus.ACTIVE:
        await callback.answer()
        await send_closed(bot, callback.message.chat.id, runtime_settings)
        return
    if not await _assert_consent(bot, callback.message.chat.id, user, runtime_settings):
        await callback.answer()
        return
    organization = await orgs_repo.get(session, organization_id)
    if (
        organization is None
        or not organization.is_active
        or not await raffles_repo.is_organization_enabled(session, raffle.id, organization.id)
    ):
        await callback.answer("Организация недоступна", show_alert=True)
        return

    existing = await apps_repo.latest_draft(session, raffle.id, user.id)
    if existing is not None:
        await state.update_data(pending_org_id=organization.id)
        await callback.answer()
        await notifications.send_text(
            bot,
            callback.message.chat.id,
            "У вас есть незавершённая заявка\\. Продолжить её или начать новую?",
            reply_markup=draft_keyboard(),
        )
        return

    try:
        application = await applications_service.start_draft(
            session,
            user=user,
            raffle=raffle,
            organization=organization,
            now=now,
        )
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer()
    await _continue_application(
        session,
        bot,
        application=application,
        raffle=raffle,
        settings=runtime_settings,
        chat_id=callback.message.chat.id,
        state=state,
        now=now,
    )


@router.callback_query(F.data.startswith("method:"))
async def on_payment_method_selected(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    runtime_settings: RuntimeSettings,
    state: FSMContext,
    now: datetime,
) -> None:
    method_id = int(callback.data.split(":", 1)[1])
    if callback.message is None:
        return
    raffle = await current_raffle(session, now)
    draft = await _current_draft(session, user, state)
    if raffle is None or draft is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    method = await orgs_repo.get_payment_method(session, method_id)
    if method is None:
        await callback.answer("Способ оплаты не найден", show_alert=True)
        return
    try:
        await applications_service.choose_payment_method(
            session, application=draft, method=method, now=now
        )
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer()
    await _show_instructions(
        session,
        bot,
        application=draft,
        raffle=raffle,
        settings=runtime_settings,
        chat_id=callback.message.chat.id,
        state=state,
    )


@router.callback_query(F.data == "flow:back_orgs")
async def on_back_to_organizations(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    runtime_settings: RuntimeSettings,
    state: FSMContext,
    now: datetime,
) -> None:
    await callback.answer()
    await state.clear()
    if callback.message:
        await _show_organizations(
            session,
            bot,
            user=user,
            settings=runtime_settings,
            chat_id=callback.message.chat.id,
            now=now,
        )


@router.callback_query(F.data == "flow:back_methods")
async def on_back_to_methods(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    runtime_settings: RuntimeSettings,
    state: FSMContext,
    now: datetime,
) -> None:
    if callback.message is None:
        return
    draft = await _current_draft(session, user, state)
    if draft is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    methods = await orgs_repo.load_payment_methods(session, draft.organization_id, only_active=True)
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        ru.payment_methods_text(draft.organization.name),
        reply_markup=payment_methods_keyboard([(method.id, method.title) for method in methods]),
    )


@router.callback_query(F.data == "flow:change_amount")
async def on_change_amount(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    draft = await _current_draft(session, user, state)
    if callback.message is None:
        return
    if draft is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await state.set_state(ParticipantFlow.amount)
    await state.update_data(application_id=draft.id)
    await callback.answer()
    await notifications.send_text(
        bot,
        callback.message.chat.id,
        "Введите сумму в BYN, которую вы отправили организации:",
    )


@router.callback_query(F.data == "flow:cancel")
async def on_cancel_flow(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    now: datetime,
) -> None:
    draft = await _current_draft(session, user, state)
    await state.clear()
    if draft is not None:
        try:
            await applications_service.cancel_by_user(
                session, application=draft, user_id=user.id, now=now
            )
            await session.commit()
        except ServiceError as exc:
            await callback.answer(exc.user_message, show_alert=True)
            return
    await callback.answer("Заявка отменена")
    if callback.message:
        await notifications.send_text(
            bot, callback.message.chat.id, "Заявка отменена\\.", reply_markup=None
        )


@router.callback_query(F.data == "flow:new")
async def on_new_application(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    runtime_settings: RuntimeSettings,
    state: FSMContext,
    now: datetime,
) -> None:
    await callback.answer()
    await state.clear()
    if callback.message:
        await _show_organizations(
            session,
            bot,
            user=user,
            settings=runtime_settings,
            chat_id=callback.message.chat.id,
            now=now,
        )


@router.callback_query(F.data == "draft:continue")
async def on_draft_continue(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    runtime_settings: RuntimeSettings,
    state: FSMContext,
    now: datetime,
) -> None:
    raffle = await current_raffle(session, now)
    draft = await _current_draft(session, user, state)
    if callback.message is None:
        return
    if raffle is None or draft is None:
        await callback.answer("Черновик не найден", show_alert=True)
        return
    await callback.answer()
    await _continue_application(
        session,
        bot,
        application=draft,
        raffle=raffle,
        settings=runtime_settings,
        chat_id=callback.message.chat.id,
        state=state,
        now=now,
    )


@router.callback_query(F.data == "draft:restart")
async def on_draft_restart(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    runtime_settings: RuntimeSettings,
    state: FSMContext,
    now: datetime,
) -> None:
    data = await state.get_data()
    pending_org_id = data.get("pending_org_id")
    raffle = await current_raffle(session, now)
    draft = await _current_draft(session, user, state)
    if callback.message is None:
        return
    if draft is not None:
        await applications_service.cancel_by_user(
            session, application=draft, user_id=user.id, now=now
        )
    await state.clear()
    if raffle is None or not pending_org_id:
        await session.commit()
        await callback.answer()
        await _show_organizations(
            session,
            bot,
            user=user,
            settings=runtime_settings,
            chat_id=callback.message.chat.id,
            now=now,
        )
        return
    organization = await orgs_repo.get(session, int(pending_org_id))
    if organization is None:
        await callback.answer("Организация недоступна", show_alert=True)
        return
    try:
        application = await applications_service.start_draft(
            session, user=user, raffle=raffle, organization=organization, now=now
        )
        await session.commit()
    except ServiceError as exc:
        await callback.answer(exc.user_message, show_alert=True)
        return
    await callback.answer()
    await _continue_application(
        session,
        bot,
        application=application,
        raffle=raffle,
        settings=runtime_settings,
        chat_id=callback.message.chat.id,
        state=state,
        now=now,
    )


@router.callback_query(F.data == "draft:cancel")
async def on_draft_cancel(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    user: User,
    runtime_settings: RuntimeSettings,
    state: FSMContext,
    now: datetime,
) -> None:
    draft = await _current_draft(session, user, state)
    if draft is not None:
        await applications_service.cancel_by_user(
            session, application=draft, user_id=user.id, now=now
        )
        await session.commit()
    await state.clear()
    await callback.answer("Черновик отменён")
    if callback.message:
        await _show_organizations(
            session,
            bot,
            user=user,
            settings=runtime_settings,
            chat_id=callback.message.chat.id,
            now=now,
        )


async def _current_draft(
    session: AsyncSession, user: User, state: FSMContext
) -> Application | None:
    data = await state.get_data()
    application_id = data.get("application_id")
    if application_id:
        application = await apps_repo.get(session, int(application_id))
        if application is not None and application.user_id == user.id:
            return application
    raffle = await raffles_repo.get_current(session)
    if raffle is None:
        return None
    draft = await apps_repo.latest_draft(session, raffle.id, user.id)
    if draft is not None:
        return draft
    for application in await apps_repo.open_for_user(session, raffle.id, user.id):
        if application.status.value == "revision_requested":
            return application
    return None


@router.message(StateFilter(ParticipantFlow.amount), F.text, ~F.text.startswith("/"))
async def on_amount(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    user: User,
    runtime_settings: RuntimeSettings,
    state: FSMContext,
    limiter: RateLimiter,
    now: datetime,
) -> None:
    if not limiter.allow("amount", user.id, limit=10, window_seconds=60):
        await notifications.send_text(
            bot, message.chat.id, "Слишком много попыток\\. Подождите минуту\\."
        )
        return
    draft = await _current_draft(session, user, state)
    if draft is None:
        await state.clear()
        await notifications.send_text(
            bot, message.chat.id, "Заявка не найдена\\. Начните с /start\\."
        )
        return
    raffle = await raffles_repo.get(session, draft.raffle_id)
    if raffle is None:
        await state.clear()
        return
    if raffle.end_at <= now or raffle.status is not RaffleStatus.ACTIVE:
        await state.clear()
        await send_closed(bot, message.chat.id, runtime_settings)
        return

    try:
        amount = parse_amount(
            message.text or "",
            min_amount=raffle.min_amount,
            max_amount=runtime_settings.max_donation,
        )
    except AmountError as exc:
        await notifications.send_text(
            bot,
            message.chat.id,
            ru.invalid_amount_text(
                exc.code.value,
                min_amount=raffle.min_amount,
                max_amount=runtime_settings.max_donation,
            ),
            reply_markup=instructions_keyboard(),
        )
        return

    preview = await applications_service.preview_for(
        session, application=draft, entered_amount=amount, ticket_price=raffle.ticket_price
    )
    try:
        await applications_service.set_amount(
            session,
            application=draft,
            amount=amount,
            now=now,
            min_amount=raffle.min_amount,
            max_amount=runtime_settings.max_donation,
        )
        await session.commit()
    except RaffleNotAcceptingError:
        await state.clear()
        await send_closed(bot, message.chat.id, runtime_settings)
        return
    except ServiceError as exc:
        await notifications.send_text(bot, message.chat.id, escape_markdown_v2(exc.user_message))
        return

    await state.set_state(ParticipantFlow.receipt)
    await state.update_data(application_id=draft.id)
    text = ru.amount_preview_text(
        amount=amount,
        current_amount=preview.current_amount,
        preview_amount=preview.preview_amount,
        preview_tickets=preview.preview_tickets,
        preview_delta=preview.delta,
    )
    suggestion = suggest_amount(preview, raffle.ticket_price, raffle.min_amount)
    if suggestion is not None and suggestion <= runtime_settings.max_donation:
        text += (
            "\n\n💡 Чтобы использовать остаток полностью, можно отправить "
            f"{escape_markdown_v2(format_money(suggestion))} BYN\\."
        )
    await notifications.send_text(bot, message.chat.id, text, reply_markup=receipt_keyboard())
