"""Raffle integrity checks for the admin panel."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ApplicationStatus
from bot.db.models import Application, ParticipantTotal, Raffle
from bot.domain.tickets import calculate_tickets


@dataclass(frozen=True)
class IntegrityIssue:
    code: str
    message: str
    severity: str = "error"
    details: dict | None = None


@dataclass
class IntegrityReport:
    raffle_id: int
    raffle_title: str
    last_ticket_number: int
    confirmed_tickets_sum: int
    participant_tickets_sum: int
    issues: list[IntegrityIssue] = field(default_factory=list)

    @property
    def is_ok(self) -> bool:
        return not self.issues

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "error")


async def check_raffle(session: AsyncSession, raffle: Raffle) -> IntegrityReport:
    confirmed_sum = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(Application.tickets_awarded), 0)).where(
                    Application.raffle_id == raffle.id,
                    Application.status == ApplicationStatus.CONFIRMED,
                )
            )
        ).scalar_one()
    )
    participant_sum = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(ParticipantTotal.tickets_issued), 0)).where(
                    ParticipantTotal.raffle_id == raffle.id
                )
            )
        ).scalar_one()
    )
    report = IntegrityReport(
        raffle_id=raffle.id,
        raffle_title=raffle.title,
        last_ticket_number=raffle.last_ticket_number,
        confirmed_tickets_sum=confirmed_sum,
        participant_tickets_sum=participant_sum,
    )

    if confirmed_sum != raffle.last_ticket_number:
        report.issues.append(
            IntegrityIssue(
                "counter_vs_applications",
                "Счётчик розыгрыша не совпадает с суммой билетов подтверждённых заявок.",
                details={"counter": raffle.last_ticket_number, "sum": confirmed_sum},
            )
        )
    if participant_sum != raffle.last_ticket_number:
        report.issues.append(
            IntegrityIssue(
                "counter_vs_participants",
                "Счётчик розыгрыша не совпадает с суммой билетов участников.",
                details={"counter": raffle.last_ticket_number, "sum": participant_sum},
            )
        )

    await _check_participants(session, raffle, report)
    await _check_ranges(session, raffle, report)
    await _check_voided(session, raffle, report)
    return report


async def _check_participants(
    session: AsyncSession, raffle: Raffle, report: IntegrityReport
) -> None:
    totals = list(
        (
            await session.execute(
                select(ParticipantTotal).where(ParticipantTotal.raffle_id == raffle.id)
            )
        ).scalars()
    )
    amounts = dict(
        (
            await session.execute(
                select(Application.user_id, func.coalesce(func.sum(Application.amount), 0))
                .where(
                    Application.raffle_id == raffle.id,
                    Application.status == ApplicationStatus.CONFIRMED,
                )
                .group_by(Application.user_id)
            )
        ).all()
    )
    for total in totals:
        expected_tickets = calculate_tickets(total.confirmed_amount, raffle.ticket_price)
        if total.tickets_issued != expected_tickets:
            report.issues.append(
                IntegrityIssue(
                    "participant_ticket_mismatch",
                    "Количество билетов участника не соответствует подтверждённой сумме.",
                    details={
                        "user_id": total.user_id,
                        "tickets": total.tickets_issued,
                        "expected": expected_tickets,
                        "amount": str(total.confirmed_amount),
                    },
                )
            )
        expected_amount = Decimal(amounts.get(total.user_id, 0))
        if Decimal(total.confirmed_amount) != expected_amount:
            report.issues.append(
                IntegrityIssue(
                    "participant_amount_mismatch",
                    "Подтверждённая сумма участника не совпадает с суммой подтверждённых заявок.",
                    details={
                        "user_id": total.user_id,
                        "amount": str(total.confirmed_amount),
                        "expected": str(expected_amount),
                    },
                )
            )


async def _check_ranges(session: AsyncSession, raffle: Raffle, report: IntegrityReport) -> None:
    applications = list(
        (
            await session.execute(
                select(Application)
                .where(
                    Application.raffle_id == raffle.id,
                    Application.status == ApplicationStatus.CONFIRMED,
                )
                .order_by(Application.first_ticket_number)
            )
        ).scalars()
    )
    ranges: list[tuple[int, int, int]] = []
    for app in applications:
        if app.first_ticket_number is None or app.last_ticket_number is None:
            report.issues.append(
                IntegrityIssue(
                    "confirmed_missing_range",
                    "У подтверждённой заявки отсутствует диапазон номеров.",
                    details={"application_id": app.id},
                )
            )
            continue
        if app.last_ticket_number - app.first_ticket_number + 1 != app.tickets_awarded:
            report.issues.append(
                IntegrityIssue(
                    "application_range_length",
                    "Длина диапазона не совпадает с количеством выданных билетов.",
                    details={
                        "application_id": app.id,
                        "first": app.first_ticket_number,
                        "last": app.last_ticket_number,
                        "tickets": app.tickets_awarded,
                    },
                )
            )
        ranges.append((app.first_ticket_number, app.last_ticket_number, app.id))

    expected_next = 1
    for first, last, application_id in sorted(ranges):
        if first > expected_next:
            report.issues.append(
                IntegrityIssue(
                    "ticket_gap",
                    "В нумерации билетов есть пропуск.",
                    details={
                        "expected": expected_next,
                        "found": first,
                        "application_id": application_id,
                    },
                )
            )
        elif first < expected_next:
            report.issues.append(
                IntegrityIssue(
                    "ticket_overlap",
                    "Диапазоны билетов пересекаются.",
                    details={
                        "expected": expected_next,
                        "found": first,
                        "application_id": application_id,
                    },
                )
            )
        expected_next = max(expected_next, last + 1)
    if ranges and expected_next - 1 != raffle.last_ticket_number:
        report.issues.append(
            IntegrityIssue(
                "range_counter_mismatch",
                "Последний номер диапазона не совпадает со счётчиком розыгрыша.",
                details={"max_range": expected_next - 1, "counter": raffle.last_ticket_number},
            )
        )


async def _check_voided(session: AsyncSession, raffle: Raffle, report: IntegrityReport) -> None:
    voided = list(
        (
            await session.execute(
                select(Application).where(
                    Application.raffle_id == raffle.id,
                    Application.status == ApplicationStatus.VOIDED,
                )
            )
        ).scalars()
    )
    for app in voided:
        if (
            app.first_ticket_number is not None
            or app.last_ticket_number is not None
            or app.voided_first_ticket_number is None
            or app.voided_last_ticket_number is None
        ):
            report.issues.append(
                IntegrityIssue(
                    "voided_range_state",
                    "У аннулированной заявки неверное состояние диапазонов.",
                    details={"application_id": app.id},
                )
            )
