"""Application review cards routed to organization and management chats."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ApplicationStatus, RoleName
from bot.db.models import Application
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import participants as participants_repo
from bot.db.repositories import roles as roles_repo
from bot.domain.tickets import build_preview
from bot.keyboards.review import review_actions
from bot.logging import get_logger
from bot.services import notifications
from bot.services.errors import NoReviewerAvailableError, ServiceError
from bot.services.settings import RuntimeSettings
from bot.services.storage import ReceiptStorage
from bot.services.users import mention
from bot.texts.markdown import CAPTION_LIMIT, escape_markdown_v2
from bot.texts.ru import admin_card_text

logger = get_logger(__name__)


class CardDeliveryError(ServiceError):
    user_message = "Не удалось отправить карточку заявки в чат проверки."


@dataclass(frozen=True)
class CardTarget:
    chat_id: int
    thread_id: int | None
    is_management_fallback: bool


async def has_second_reviewer(
    session: AsyncSession,
    application: Application,
    *,
    env_admin_ids: frozenset[int],
) -> bool:
    """Return whether someone other than the participant can review the card."""
    org_reps = await roles_repo.active_holders(
        session, RoleName.ORG_REP, organization_id=application.organization_id
    )
    organizers = await roles_repo.active_holders(session, RoleName.ORGANIZER)
    candidate_ids = {role.user_id for role in org_reps}
    candidate_ids.update(role.user_id for role in organizers)
    candidate_ids.update(env_admin_ids)
    candidate_ids.discard(application.user_id)
    return bool(candidate_ids)


async def resolve_target(
    session: AsyncSession,
    application: Application,
    settings: RuntimeSettings,
    *,
    env_admin_ids: frozenset[int],
) -> CardTarget:
    organization = application.organization
    if await has_second_reviewer(session, application, env_admin_ids=env_admin_ids):
        return CardTarget(
            chat_id=organization.review_chat_id,
            thread_id=organization.review_thread_id,
            is_management_fallback=False,
        )
    if settings.management_log_chat_id:
        return CardTarget(
            chat_id=settings.management_log_chat_id,
            thread_id=None,
            is_management_fallback=True,
        )
    raise NoReviewerAvailableError(
        "Единственный проверяющий — сам участник. Нужен второй проверяющий или чат "
        "управления (MANAGEMENT_LOG_CHAT_ID)."
    )


async def build_card_text(
    session: AsyncSession,
    application: Application,
    settings: RuntimeSettings,
    *,
    now: datetime,
) -> str:
    total = await participants_repo.get(session, application.raffle_id, application.user_id)
    current_amount = total.confirmed_amount if total else 0
    current_tickets = total.tickets_issued if total else 0
    preview = build_preview(
        application.amount or Decimal("0.00"),
        current_amount=current_amount,
        current_tickets=current_tickets,
        ticket_price=application.raffle.ticket_price,
    )
    return admin_card_text(
        public_id=application.public_id,
        payment_code=application.payment_code,
        user_mention=mention(application.user),
        organization_name=application.organization.name,
        payment_method_title=application.payment_method_title or "—",
        amount=application.amount or Decimal("0.00"),
        current_amount=current_amount,
        preview_delta=preview.delta,
        preview_tickets=preview.preview_tickets,
        submitted_at=application.submitted_at,
        tz=settings.timezone,
        raffle_title=application.raffle.title,
    )


async def send_application_card(
    session: AsyncSession,
    bot: Bot,
    *,
    application: Application,
    settings: RuntimeSettings,
    storage: ReceiptStorage,
    env_admin_ids: frozenset[int],
    now: datetime,
    keyboard: InlineKeyboardMarkup | None = None,
) -> CardTarget:
    target = await resolve_target(session, application, settings, env_admin_ids=env_admin_ids)
    caption = await build_card_text(session, application, settings, now=now)
    markup = keyboard or review_actions(application.id)

    try:
        if len(caption) <= CAPTION_LIMIT:
            message = await _send_receipt(
                bot,
                chat=target,
                application=application,
                caption=caption,
                keyboard=markup,
                storage=storage,
            )
        else:
            message = await _send_receipt(
                bot,
                chat=target,
                application=application,
                caption=None,
                keyboard=None,
                storage=storage,
            )
            text_result = await notifications.send_text(
                bot,
                target.chat_id,
                caption,
                reply_markup=markup,
                message_thread_id=target.thread_id,
            )
            if not text_result.ok:
                raise CardDeliveryError(text_result.error or "card text failed")
    except Exception as exc:
        await audit_repo.log(
            session,
            entity_type="application",
            entity_id=application.id,
            application_id=application.id,
            raffle_id=application.raffle_id,
            actor_id=None,
            action="card_send_failed",
            payload={"error": str(exc)[:500]},
            at=now,
        )
        raise CardDeliveryError(str(exc)) from exc

    application.card_chat_id = target.chat_id
    application.card_message_id = message.message_id
    await session.flush()
    await audit_repo.log(
        session,
        entity_type="application",
        entity_id=application.id,
        application_id=application.id,
        raffle_id=application.raffle_id,
        actor_id=None,
        action="card_sent",
        payload={
            "chat_id": target.chat_id,
            "message_id": message.message_id,
            "management_fallback": target.is_management_fallback,
        },
        at=now,
    )
    await _send_management_copy(bot, application, settings, target)

    if not await has_second_reviewer(session, application, env_admin_ids=env_admin_ids):
        await notifications.notify_admins(
            bot,
            env_admin_ids,
            "⚠️ По заявке "
            f"`{application.public_id}` единственный проверяющий — сам участник. "
            "Нужен второй проверяющий.",
        )
    return target


async def _send_management_copy(
    bot: Bot, application: Application, settings: RuntimeSettings, target: CardTarget
) -> None:
    chat_id = settings.management_log_chat_id
    if not chat_id or chat_id == target.chat_id:
        return
    text = (
        "Копия карточки заявки "
        f"`{application.public_id}` в организации "
        f"«{escape_markdown_v2(application.organization.name)}»\\. "
        "Действия доступны в чате проверки\\."
    )
    await notifications.send_text(bot, chat_id, text)


async def _send_receipt(
    bot: Bot,
    *,
    chat: CardTarget,
    application: Application,
    caption: str | None,
    keyboard: InlineKeyboardMarkup | None,
    storage: ReceiptStorage,
):
    kwargs: dict = {}
    if caption is not None:
        kwargs["caption"] = caption
        kwargs["parse_mode"] = "MarkdownV2"
    if keyboard is not None:
        kwargs["reply_markup"] = keyboard
    if chat.thread_id is not None:
        kwargs["message_thread_id"] = chat.thread_id

    try:
        return await _send_by_file_id(bot, chat.chat_id, application, **kwargs)
    except TelegramBadRequest as exc:
        logger.warning("file_id delivery failed for %s: %s", application.public_id, exc)

    if not application.receipt_object_key:
        raise CardDeliveryError("Telegram file_id unavailable and no object key")

    data = await storage.get(application.receipt_object_key)
    suffix = (application.receipt_object_key or "").rsplit(".", 1)[-1]
    filename = f"receipt.{suffix}" if suffix in {"jpg", "png", "pdf"} else "receipt"
    upload = BufferedInputFile(data, filename=filename)
    if application.receipt_type and application.receipt_type.value == "photo":
        return await bot.send_photo(chat.chat_id, photo=upload, **kwargs)
    return await bot.send_document(chat.chat_id, document=upload, **kwargs)


async def _send_by_file_id(bot: Bot, chat_id: int, application: Application, **kwargs):
    if not application.receipt_file_id:
        raise CardDeliveryError("Telegram file_id is missing")
    if application.receipt_type and application.receipt_type.value == "photo":
        return await bot.send_photo(chat_id, photo=application.receipt_file_id, **kwargs)
    return await bot.send_document(chat_id, document=application.receipt_file_id, **kwargs)


async def edit_review_card(
    bot: Bot, application: Application, text: str, *, caption: bool | None = None
) -> None:
    if application.card_chat_id is None or application.card_message_id is None:
        return
    if not text.strip():
        return
    truncated = text[:CAPTION_LIMIT]
    is_caption = caption if caption is not None else application.receipt_type is not None
    try:
        if is_caption:
            await bot.edit_message_caption(
                chat_id=application.card_chat_id,
                message_id=application.card_message_id,
                caption=truncated,
                parse_mode="MarkdownV2",
                reply_markup=None,
            )
        else:
            await bot.edit_message_text(
                chat_id=application.card_chat_id,
                message_id=application.card_message_id,
                text=truncated,
                parse_mode="MarkdownV2",
                reply_markup=None,
            )
    except TelegramBadRequest as exc:
        logger.warning("Could not edit review card %s: %s", application.public_id, exc)


async def send_receipt_to_chat(
    bot: Bot,
    *,
    chat_id: int,
    application: Application,
    storage: ReceiptStorage,
    thread_id: int | None = None,
) -> None:
    kwargs: dict = {}
    if thread_id is not None:
        kwargs["message_thread_id"] = thread_id
    try:
        await _send_by_file_id(bot, chat_id, application, **kwargs)
        return
    except TelegramBadRequest:
        pass
    if not application.receipt_object_key:
        raise CardDeliveryError("Чек недоступен: нет file_id и объекта в RustFS.")
    data = await storage.get(application.receipt_object_key)
    upload = BufferedInputFile(data, filename="receipt")
    if application.receipt_type and application.receipt_type.value == "photo":
        await bot.send_photo(chat_id, photo=upload, **kwargs)
    else:
        await bot.send_document(chat_id, document=upload, **kwargs)


async def resend_missing_cards(
    session: AsyncSession,
    bot: Bot,
    *,
    settings: RuntimeSettings,
    storage: ReceiptStorage,
    env_admin_ids: frozenset[int],
    now: datetime,
    limit: int = 10,
) -> int:
    """Retry cards whose first delivery failed. Applications are never lost."""
    from bot.db.repositories import applications as apps_repo

    filters = apps_repo.ApplicationFilters(
        statuses=(ApplicationStatus.PENDING_REVIEW, ApplicationStatus.REVISION_REQUESTED)
    )
    applications = await apps_repo.list_filtered(session, filters, limit=100)
    delivered = 0
    for application in applications:
        if application.card_message_id is not None:
            continue
        try:
            await send_application_card(
                session,
                bot,
                application=application,
                settings=settings,
                storage=storage,
                env_admin_ids=env_admin_ids,
                now=now,
            )
            await session.commit()
            delivered += 1
        except Exception as exc:
            logger.warning("Card retry failed for %s: %s", application.public_id, exc)
            await session.rollback()
        if delivered >= limit:
            break
    return delivered
