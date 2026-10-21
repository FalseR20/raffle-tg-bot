"""Participant application flow (drafts, amount, receipts, cancellation)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ApplicationStatus, RaffleStatus, ReceiptType
from bot.db.models import (
    Application,
    Organization,
    PaymentMethod,
    Raffle,
    User,
)
from bot.db.repositories import applications as apps_repo
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import organizations as orgs_repo
from bot.db.repositories import participants as participants_repo
from bot.db.repositories import raffles as raffles_repo
from bot.domain.tickets import TicketPreview, build_preview, calculate_tickets
from bot.services.errors import (
    AlreadyProcessedError,
    InvalidStateError,
    NotFoundError,
    RaffleNotAcceptingError,
    ValidationError,
)

PAYMENT_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAYMENT_CODE_LENGTH = 5
MAX_APPLICATIONS_PER_RAFFLE = 20
DRAFT_TTL = timedelta(hours=24)


async def generate_payment_code(session: AsyncSession, raffle_id: int) -> str:
    for _ in range(50):
        code = "".join(secrets.choice(PAYMENT_CODE_ALPHABET) for _ in range(PAYMENT_CODE_LENGTH))
        if not await apps_repo.payment_code_exists(session, raffle_id, code):
            return code
    raise RuntimeError("could not allocate unique payment code")  # pragma: no cover


def raffle_accepts(raffle: Raffle, now: datetime) -> bool:
    return raffle.status is RaffleStatus.ACTIVE and raffle.end_at > now


async def start_draft(
    session: AsyncSession,
    *,
    user: User,
    raffle: Raffle,
    organization: Organization,
    now: datetime,
    replace_existing: bool = False,
) -> Application:
    if user.is_blocked:
        raise InvalidStateError("Учётная запись заблокирована.")
    if not raffle_accepts(raffle, now):
        raise RaffleNotAcceptingError()
    if not await raffles_repo.is_organization_enabled(session, raffle.id, organization.id):
        raise ValidationError("Эта организация не участвует в розыгрыше.")
    if not organization.is_active:
        raise ValidationError("Организация недоступна.")
    methods = await orgs_repo.load_payment_methods(session, organization.id, only_active=True)
    if not methods:
        raise ValidationError("У организации нет активных способов оплаты.")

    existing = await apps_repo.latest_draft(session, raffle.id, user.id)
    if existing is not None:
        if not replace_existing:
            raise InvalidStateError("У вас уже есть незавершённая заявка.")
        await cancel_by_user(session, application=existing, user_id=user.id, now=now)

    applications = await apps_repo.for_user(session, raffle.id, user.id)
    if len(applications) >= MAX_APPLICATIONS_PER_RAFFLE:
        raise ValidationError("Достигнут лимит заявок в этом розыгрыше.")

    application = await apps_repo.create(
        session,
        raffle_id=raffle.id,
        user_id=user.id,
        organization_id=organization.id,
        payment_code=await generate_payment_code(session, raffle.id),
        status=ApplicationStatus.DRAFT,
        currency="BYN",
        amount=None,
        tickets_awarded=0,
        created_at=now,
    )
    # Create the totals row before any review transaction so that confirmation
    # never inserts a child row while competing for the raffle row lock.
    await participants_repo.ensure(session, raffle.id, user.id)
    await audit_repo.log(
        session,
        entity_type="application",
        entity_id=application.id,
        application_id=application.id,
        raffle_id=raffle.id,
        actor_id=user.id,
        action="application_draft_created",
        new_status=ApplicationStatus.DRAFT.value,
        payload={"organization_id": organization.id, "payment_code": application.payment_code},
        at=now,
    )
    return application


async def choose_payment_method(
    session: AsyncSession,
    *,
    application: Application,
    method: PaymentMethod,
    now: datetime,
) -> Application:
    if application.status not in {
        ApplicationStatus.DRAFT,
        ApplicationStatus.AWAITING_RECEIPT,
    }:
        raise InvalidStateError("Способ оплаты уже нельзя изменить.")
    if method.organization_id != application.organization_id or not method.is_active:
        raise ValidationError("Способ оплаты недоступен.")
    application.payment_method_id = method.id
    application.payment_method_title = method.title
    application.payment_instructions_snapshot = method.instructions_text
    await session.flush()
    return application


async def set_amount(
    session: AsyncSession,
    *,
    application: Application,
    amount: Decimal,
    now: datetime,
    min_amount: Decimal,
    max_amount: Decimal,
) -> None:
    if application.status not in {
        ApplicationStatus.DRAFT,
        ApplicationStatus.AWAITING_RECEIPT,
    }:
        raise InvalidStateError("Сумму уже нельзя изменить.")
    raffle = await raffles_repo.get(session, application.raffle_id)
    if raffle is None:
        raise NotFoundError("Розыгрыш не найден.")
    if not raffle_accepts(raffle, now):
        raise RaffleNotAcceptingError()
    if application.payment_method_id is None:
        raise InvalidStateError("Сначала выберите способ оплаты.")
    if application.created_at + DRAFT_TTL <= now:
        raise InvalidStateError("Черновик устарел. Начните новую заявку.")
    if amount < min_amount:
        raise ValidationError("Сумма меньше минимальной.")
    if amount > max_amount:
        raise ValidationError("Сумма больше максимальной.")

    old_status = application.status
    application.amount = amount
    application.status = ApplicationStatus.AWAITING_RECEIPT
    await session.flush()
    await audit_repo.log(
        session,
        entity_type="application",
        entity_id=application.id,
        application_id=application.id,
        raffle_id=application.raffle_id,
        actor_id=application.user_id,
        action="application_amount_set",
        old_status=old_status.value,
        new_status=application.status.value,
        payload={"amount": str(amount)},
        at=now,
    )


async def preview_for(
    session: AsyncSession,
    *,
    application: Application,
    entered_amount: Decimal,
    ticket_price: Decimal,
) -> TicketPreview:
    total = await participants_repo.get(session, application.raffle_id, application.user_id)
    current_amount = total.confirmed_amount if total else Decimal("0.00")
    current_tickets = total.tickets_issued if total else 0
    return build_preview(
        entered_amount,
        current_amount=current_amount,
        current_tickets=current_tickets,
        ticket_price=ticket_price,
    )


async def attach_receipt(
    session: AsyncSession,
    *,
    application: Application,
    receipt_type: ReceiptType,
    file_id: str,
    file_unique_id: str,
    object_key: str,
    etag: str | None,
    mime: str,
    size: int,
    now: datetime,
) -> Application:
    old_status = application.status
    if old_status not in {ApplicationStatus.AWAITING_RECEIPT, ApplicationStatus.REVISION_REQUESTED}:
        raise InvalidStateError("Сейчас нельзя прикрепить чек.")
    application.receipt_type = receipt_type
    application.receipt_file_id = file_id
    application.receipt_file_unique_id = file_unique_id
    application.receipt_object_key = object_key
    application.receipt_etag = etag
    application.receipt_mime = mime
    application.receipt_size = size
    application.status = ApplicationStatus.PENDING_REVIEW
    if application.submitted_at is None:
        application.submitted_at = now
    application.reviewed_by = None
    application.reviewed_at = None
    application.rejection_reason = None
    application.notification_error = None
    application.card_chat_id = None
    application.card_message_id = None
    await session.flush()
    await audit_repo.log(
        session,
        entity_type="application",
        entity_id=application.id,
        application_id=application.id,
        raffle_id=application.raffle_id,
        actor_id=application.user_id,
        action="receipt_uploaded",
        old_status=old_status.value,
        new_status=application.status.value,
        payload={
            "object_key": object_key,
            "size": size,
            "mime": mime,
            "receipt_type": receipt_type.value,
        },
        at=now,
    )
    return application


async def cancel_by_user(
    session: AsyncSession,
    *,
    application: Application,
    user_id: int,
    now: datetime,
) -> Application:
    locked = await apps_repo.get_locked(session, application.id)
    if locked is None:
        raise NotFoundError("Заявка не найдена.")
    if locked.user_id != user_id:
        raise NotFoundError("Заявка не найдена.")
    if not locked.status.is_cancellable_by_user:
        raise AlreadyProcessedError(
            "Заявку уже нельзя отменить: она обработана. Текущий статус показан в «Мои заявки»."
        )
    old_status = locked.status
    locked.status = ApplicationStatus.CANCELLED
    await session.flush()
    await audit_repo.log(
        session,
        entity_type="application",
        entity_id=locked.id,
        application_id=locked.id,
        raffle_id=locked.raffle_id,
        actor_id=user_id,
        action="application_cancelled",
        old_status=old_status.value,
        new_status=ApplicationStatus.CANCELLED.value,
        at=now,
    )
    return locked


async def expire_drafts(
    session: AsyncSession, *, now: datetime, ttl: timedelta = DRAFT_TTL
) -> list[Application]:
    """Expire only drafts, never submitted applications."""
    expired: list[Application] = []
    for application in await apps_repo.drafts_older_than(session, now - ttl):
        old_status = application.status
        application.status = ApplicationStatus.EXPIRED
        expired.append(application)
        await audit_repo.log(
            session,
            entity_type="application",
            entity_id=application.id,
            application_id=application.id,
            raffle_id=application.raffle_id,
            actor_id=None,
            action="application_expired",
            old_status=old_status.value,
            new_status=ApplicationStatus.EXPIRED.value,
            payload={"reason": "draft_ttl"},
            at=now,
        )
    if expired:
        await session.flush()
    return expired


async def user_totals(
    session: AsyncSession, raffle_id: int, user_id: int
) -> tuple[Decimal, int, list[tuple[int, int]]]:
    total = await participants_repo.get(session, raffle_id, user_id)
    ranges = await apps_repo.confirmed_ranges(session, raffle_id, user_id)
    return (
        total.confirmed_amount if total else Decimal("0.00"),
        total.tickets_issued if total else 0,
        ranges,
    )


def expected_tickets(amount: Decimal, price: Decimal) -> int:
    return calculate_tickets(amount, price)
