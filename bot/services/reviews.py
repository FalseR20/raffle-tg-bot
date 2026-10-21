"""Review transactions: confirmation, rejection, revision and void.

Lock order for every financial operation:
1. application row;
2. participant totals row;
3. raffle row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ApplicationStatus, RaffleStatus
from bot.db.models import Application, Raffle
from bot.db.repositories import applications as apps_repo
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import participants as participants_repo
from bot.db.repositories import raffles as raffles_repo
from bot.domain.tickets import TicketRange, assign_range, calculate_tickets
from bot.services.errors import (
    AlreadyProcessedError,
    IntegrityError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    SelfReviewError,
)
from bot.services.permissions import Permissions


@dataclass(frozen=True)
class ConfirmationResult:
    application: Application
    raffle: Raffle
    range: TicketRange
    total_amount: Decimal
    total_tickets: int
    all_ranges: list[tuple[int, int]]


@dataclass(frozen=True)
class VoidResult:
    application: Application
    raffle: Raffle
    voided_range: TicketRange
    total_amount: Decimal
    total_tickets: int


@dataclass(frozen=True)
class ReviewResult:
    application: Application
    previous_status: ApplicationStatus


async def _load_application_locked(session: AsyncSession, application_id: int) -> Application:
    application = await apps_repo.get_locked(session, application_id)
    if application is None:
        raise NotFoundError("Заявка не найдена.")
    return application


def _assert_reviewer_can_review(
    application: Application, *, reviewer_id: int, reviewer: Permissions
) -> None:
    if reviewer_id == application.user_id:
        raise SelfReviewError()
    reviewer.assert_review_organization(application.organization_id)


def _assert_reviewable_raffle(raffle: Raffle) -> None:
    if raffle.status not in {RaffleStatus.ACTIVE, RaffleStatus.CLOSED}:
        raise InvalidStateError("Розыгрыш уже завершён, изменения невозможны.")


def _assert_submitted_before_deadline(application: Application, raffle: Raffle) -> None:
    if application.submitted_at is None:
        raise InvalidStateError("У заявки нет даты отправки чека.")
    if application.submitted_at > raffle.end_at:
        raise InvalidStateError("Заявка отправлена после окончания приёма.")


async def confirm_application(
    session: AsyncSession,
    *,
    application_id: int,
    reviewer_id: int,
    reviewer: Permissions,
    now: datetime,
) -> ConfirmationResult:
    application = await _load_application_locked(session, application_id)
    if application.status is ApplicationStatus.CONFIRMED:
        raise AlreadyProcessedError("Заявка уже подтверждена.")
    if application.status is ApplicationStatus.VOIDED:
        raise AlreadyProcessedError("Подтверждение этой заявки было аннулировано.")
    if application.status is not ApplicationStatus.PENDING_REVIEW:
        raise InvalidStateError("Подтвердить можно только заявку на проверке.")
    _assert_reviewer_can_review(application, reviewer_id=reviewer_id, reviewer=reviewer)
    if application.amount is None:
        raise IntegrityError("У заявки нет суммы.")

    raffle = await raffles_repo.get(session, application.raffle_id)
    if raffle is None:
        raise NotFoundError("Розыгрыш не найден.")
    _assert_reviewable_raffle(raffle)
    _assert_submitted_before_deadline(application, raffle)

    totals = await participants_repo.get_locked(session, application.raffle_id, application.user_id)

    locked_raffle = await raffles_repo.lock(session, application.raffle_id)
    if locked_raffle is None:
        raise NotFoundError("Розыгрыш не найден.")
    _assert_reviewable_raffle(locked_raffle)
    raffle = locked_raffle

    new_amount = totals.confirmed_amount + application.amount
    new_tickets = calculate_tickets(new_amount, raffle.ticket_price)
    delta = new_tickets - totals.tickets_issued
    if delta < 1:
        raise IntegrityError("Расчёт не даёт новых билетов; подтверждение остановлено.")

    counter_stmt = (
        update(Raffle)
        .where(
            Raffle.id == raffle.id,
            Raffle.status.in_([RaffleStatus.ACTIVE, RaffleStatus.CLOSED]),
        )
        .values(last_ticket_number=Raffle.last_ticket_number + delta)
        .returning(Raffle.last_ticket_number)
    )
    new_last = (await session.execute(counter_stmt)).scalar_one_or_none()
    if new_last is None:
        raise InvalidStateError("Розыгрыш больше не принимает подтверждения.")
    raffle.last_ticket_number = int(new_last)
    ticket_range = assign_range(int(new_last) - delta, delta)

    totals.confirmed_amount = new_amount
    totals.tickets_issued = new_tickets
    totals.updated_at = now
    await session.flush()

    application.status = ApplicationStatus.CONFIRMED
    application.tickets_awarded = delta
    application.tickets_total_after = new_tickets
    application.first_ticket_number = ticket_range.first
    application.last_ticket_number = ticket_range.last
    application.reviewed_by = reviewer_id
    application.reviewed_at = now
    application.rejection_reason = None
    await session.flush()

    all_ranges = await apps_repo.confirmed_ranges(
        session, application.raffle_id, application.user_id
    )
    await audit_repo.log(
        session,
        entity_type="application",
        entity_id=application.id,
        application_id=application.id,
        raffle_id=application.raffle_id,
        actor_id=reviewer_id,
        action="application_confirmed",
        old_status=ApplicationStatus.PENDING_REVIEW.value,
        new_status=ApplicationStatus.CONFIRMED.value,
        payload={
            "amount": str(application.amount),
            "delta": delta,
            "first_ticket": ticket_range.first,
            "last_ticket": ticket_range.last,
            "total_amount": str(new_amount),
            "total_tickets": new_tickets,
        },
        at=now,
    )
    return ConfirmationResult(
        application=application,
        raffle=raffle,
        range=ticket_range,
        total_amount=new_amount,
        total_tickets=new_tickets,
        all_ranges=all_ranges,
    )


async def reject_application(
    session: AsyncSession,
    *,
    application_id: int,
    reviewer_id: int,
    reviewer: Permissions,
    reason: str,
    now: datetime,
) -> ReviewResult:
    application = await _load_application_locked(session, application_id)
    if application.status not in {
        ApplicationStatus.PENDING_REVIEW,
        ApplicationStatus.REVISION_REQUESTED,
    }:
        raise AlreadyProcessedError("Заявка уже обработана.")
    _assert_reviewer_can_review(application, reviewer_id=reviewer_id, reviewer=reviewer)
    raffle = await raffles_repo.lock(session, application.raffle_id)
    if raffle is None:
        raise NotFoundError("Розыгрыш не найден.")
    if raffle.status.is_finished:
        raise InvalidStateError("Розыгрыш завершён, отклонение невозможно.")

    previous = application.status
    application.status = ApplicationStatus.REJECTED
    application.rejection_reason = reason
    application.reviewed_by = reviewer_id
    application.reviewed_at = now
    await session.flush()
    await audit_repo.log(
        session,
        entity_type="application",
        entity_id=application.id,
        application_id=application.id,
        raffle_id=application.raffle_id,
        actor_id=reviewer_id,
        action="application_rejected",
        old_status=previous.value,
        new_status=ApplicationStatus.REJECTED.value,
        payload={"reason": reason},
        at=now,
    )
    return ReviewResult(application=application, previous_status=previous)


async def request_new_receipt(
    session: AsyncSession,
    *,
    application_id: int,
    reviewer_id: int,
    reviewer: Permissions,
    reason: str,
    now: datetime,
) -> ReviewResult:
    application = await _load_application_locked(session, application_id)
    if application.status is not ApplicationStatus.PENDING_REVIEW:
        raise AlreadyProcessedError("Запросить новый чек можно только у заявки на проверке.")
    _assert_reviewer_can_review(application, reviewer_id=reviewer_id, reviewer=reviewer)
    raffle = await raffles_repo.lock(session, application.raffle_id)
    if raffle is None:
        raise NotFoundError("Розыгрыш не найден.")
    if raffle.status.is_finished:
        raise InvalidStateError("Розыгрыш завершён, можно только отклонить заявку.")

    previous = application.status
    application.status = ApplicationStatus.REVISION_REQUESTED
    application.rejection_reason = reason
    application.reviewed_by = reviewer_id
    application.reviewed_at = now
    await session.flush()
    await audit_repo.log(
        session,
        entity_type="application",
        entity_id=application.id,
        application_id=application.id,
        raffle_id=application.raffle_id,
        actor_id=reviewer_id,
        action="application_revision_requested",
        old_status=previous.value,
        new_status=ApplicationStatus.REVISION_REQUESTED.value,
        payload={
            "reason": reason,
            "submitted_at": (
                application.submitted_at.isoformat() if application.submitted_at else None
            ),
        },
        at=now,
    )
    return ReviewResult(application=application, previous_status=previous)


async def void_application(
    session: AsyncSession,
    *,
    application_id: int,
    actor_id: int,
    actor: Permissions,
    reason: str,
    now: datetime,
) -> VoidResult:
    if not actor.can_manage_raffles:
        raise PermissionDeniedError("Аннулировать подтверждение может только организатор.")

    application = await _load_application_locked(session, application_id)
    if application.status is not ApplicationStatus.CONFIRMED:
        raise InvalidStateError("Аннулировать можно только подтверждённую заявку.")
    if application.last_ticket_number is None or application.first_ticket_number is None:
        raise IntegrityError("У подтверждённой заявки отсутствует диапазон номеров.")
    if application.amount is None:
        raise IntegrityError("У подтверждённой заявки отсутствует сумма.")

    raffle = await raffles_repo.get(session, application.raffle_id)
    if raffle is None:
        raise NotFoundError("Розыгрыш не найден.")
    if raffle.status.is_finished:
        raise InvalidStateError("После завершения розыгрыша аннулирование запрещено.")
    if raffle.status not in {RaffleStatus.ACTIVE, RaffleStatus.CLOSED}:
        raise InvalidStateError("Аннулирование доступно только для текущего розыгрыша.")

    totals = await participants_repo.get_locked(session, application.raffle_id, application.user_id)

    locked_raffle = await raffles_repo.lock(session, application.raffle_id)
    if locked_raffle is None:
        raise NotFoundError("Розыгрыш не найден.")
    raffle = locked_raffle
    if raffle.status.is_finished:
        raise InvalidStateError("После завершения розыгрыша аннулирование запрещено.")
    if application.last_ticket_number != raffle.last_ticket_number:
        raise InvalidStateError("Аннулировать можно только последний выданный диапазон номеров.")

    new_amount = totals.confirmed_amount - application.amount
    if new_amount < 0:
        raise IntegrityError("Отрицательная сумма у участника.")
    new_tickets = calculate_tickets(new_amount, raffle.ticket_price)
    tickets_to_remove = totals.tickets_issued - new_tickets
    if tickets_to_remove != application.tickets_awarded:
        raise IntegrityError("Количество аннулируемых билетов не совпадает с заявкой.")

    voided_range = TicketRange(
        first=application.first_ticket_number, last=application.last_ticket_number
    )
    application.voided_first_ticket_number = voided_range.first
    application.voided_last_ticket_number = voided_range.last
    application.first_ticket_number = None
    application.last_ticket_number = None
    application.status = ApplicationStatus.VOIDED
    application.reviewed_by = actor_id
    application.reviewed_at = now

    totals.confirmed_amount = new_amount
    totals.tickets_issued = new_tickets
    totals.updated_at = now

    counter_stmt = (
        update(Raffle)
        .where(
            Raffle.id == raffle.id, Raffle.status.in_([RaffleStatus.ACTIVE, RaffleStatus.CLOSED])
        )
        .values(last_ticket_number=Raffle.last_ticket_number - tickets_to_remove)
        .returning(Raffle.last_ticket_number)
    )
    new_last = (await session.execute(counter_stmt)).scalar_one_or_none()
    if new_last is None or new_last < 0:
        raise IntegrityError("Не удалось скорректировать счётчик билетов.")
    raffle.last_ticket_number = int(new_last)
    await session.flush()

    await audit_repo.log(
        session,
        entity_type="application",
        entity_id=application.id,
        application_id=application.id,
        raffle_id=application.raffle_id,
        actor_id=actor_id,
        action="application_voided",
        old_status=ApplicationStatus.CONFIRMED.value,
        new_status=ApplicationStatus.VOIDED.value,
        payload={
            "reason": reason,
            "voided_first_ticket": voided_range.first,
            "voided_last_ticket": voided_range.last,
            "removed_tickets": tickets_to_remove,
            "total_amount": str(new_amount),
            "total_tickets": new_tickets,
        },
        at=now,
    )
    return VoidResult(
        application=application,
        raffle=raffle,
        voided_range=voided_range,
        total_amount=new_amount,
        total_tickets=new_tickets,
    )
