"""Raffle statistics for the admin panel."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ApplicationStatus
from bot.db.models import Raffle
from bot.db.repositories import applications as apps_repo


@dataclass(frozen=True)
class BreakdownRow:
    name: str
    applications: int
    confirmed: int
    amount: Decimal


@dataclass
class RaffleStatistics:
    raffle_id: int
    raffle_title: str
    total_applications: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)
    confirmed_amount: Decimal = Decimal("0.00")
    tickets_issued: int = 0
    prizes_count: int = 1
    unique_participants: int = 0
    average_donation: Decimal = Decimal("0.00")
    oldest_pending_days: int = 0
    start_to_receipt_percent: Decimal = Decimal("0.00")
    receipt_to_confirmation_percent: Decimal = Decimal("0.00")
    by_organization: list[BreakdownRow] = field(default_factory=list)
    by_payment_method: list[BreakdownRow] = field(default_factory=list)


async def build_statistics(
    session: AsyncSession,
    raffle: Raffle,
    *,
    allowed_organization_ids: frozenset[int] | None = None,
) -> RaffleStatistics:
    applications = await apps_repo.list_filtered(
        session,
        apps_repo.ApplicationFilters(raffle_id=raffle.id),
        limit=100_000,
    )
    if allowed_organization_ids is not None:
        applications = [
            app for app in applications if app.organization_id in allowed_organization_ids
        ]

    stats = RaffleStatistics(
        raffle_id=raffle.id,
        raffle_title=raffle.title,
        total_applications=len(applications),
        prizes_count=raffle.prizes_count,
    )
    counts: dict[str, int] = defaultdict(int)
    confirmed = [app for app in applications if app.status is ApplicationStatus.CONFIRMED]
    submitted = [app for app in applications if app.submitted_at is not None]
    participants = {app.user_id for app in applications}

    for app in applications:
        counts[app.status.value] += 1
    stats.status_counts = dict(counts)
    stats.confirmed_amount = sum(
        (app.amount or Decimal("0.00") for app in confirmed), Decimal("0.00")
    )
    stats.tickets_issued = sum(app.tickets_awarded for app in confirmed)
    stats.unique_participants = len(participants)
    if confirmed:
        stats.average_donation = (stats.confirmed_amount / len(confirmed)).quantize(Decimal("0.01"))
    if applications:
        stats.start_to_receipt_percent = (
            Decimal(len(submitted)) * Decimal(100) / Decimal(len(applications))
        ).quantize(Decimal("0.1"))
    pending_times = [
        app.submitted_at
        for app in applications
        if app.status in {ApplicationStatus.PENDING_REVIEW, ApplicationStatus.REVISION_REQUESTED}
        and app.submitted_at is not None
    ]
    if pending_times:
        from bot.utils.time import utcnow

        stats.oldest_pending_days = max(0, (utcnow() - min(pending_times)).days)
    if submitted:
        stats.receipt_to_confirmation_percent = (
            Decimal(len(confirmed)) * Decimal(100) / Decimal(len(submitted))
        ).quantize(Decimal("0.1"))

    org_rows: dict[str, list] = defaultdict(lambda: [0, 0, Decimal("0.00")])
    method_rows: dict[str, list] = defaultdict(lambda: [0, 0, Decimal("0.00")])
    for app in applications:
        org = org_rows[app.organization.name]
        org[0] += 1
        if app.status is ApplicationStatus.CONFIRMED:
            org[1] += 1
            org[2] += app.amount or Decimal("0.00")
        method_name = app.payment_method_title or "—"
        method = method_rows[method_name]
        method[0] += 1
        if app.status is ApplicationStatus.CONFIRMED:
            method[1] += 1
            method[2] += app.amount or Decimal("0.00")

    stats.by_organization = [
        BreakdownRow(name=name, applications=values[0], confirmed=values[1], amount=values[2])
        for name, values in sorted(org_rows.items())
    ]
    stats.by_payment_method = [
        BreakdownRow(name=name, applications=values[0], confirmed=values[1], amount=values[2])
        for name, values in sorted(method_rows.items())
    ]
    return stats
