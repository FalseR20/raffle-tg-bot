"""Application repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.enums import ApplicationStatus
from bot.db.models import Application, User

OPEN_STATUSES = (
    ApplicationStatus.DRAFT,
    ApplicationStatus.AWAITING_RECEIPT,
    ApplicationStatus.PENDING_REVIEW,
    ApplicationStatus.REVISION_REQUESTED,
)


@dataclass(frozen=True)
class ApplicationFilters:
    raffle_id: int | None = None
    organization_id: int | None = None
    organization_ids: frozenset[int] | None = None
    payment_method_id: int | None = None
    status: ApplicationStatus | None = None
    statuses: tuple[ApplicationStatus, ...] | None = None
    query: str | None = None
    user_id: int | None = None


def _apply_filters(stmt, filters: ApplicationFilters):
    if filters.raffle_id is not None:
        stmt = stmt.where(Application.raffle_id == filters.raffle_id)
    if filters.organization_id is not None:
        stmt = stmt.where(Application.organization_id == filters.organization_id)
    if filters.organization_ids is not None:
        stmt = stmt.where(Application.organization_id.in_(filters.organization_ids))
    if filters.payment_method_id is not None:
        stmt = stmt.where(Application.payment_method_id == filters.payment_method_id)
    if filters.status is not None:
        stmt = stmt.where(Application.status == filters.status)
    if filters.statuses is not None:
        stmt = stmt.where(Application.status.in_(filters.statuses))
    if filters.user_id is not None:
        stmt = stmt.where(Application.user_id == filters.user_id)
    if filters.query:
        raw = filters.query.strip()
        pattern = f"%{raw}%"
        conditions = [
            Application.public_id.ilike(pattern),
            Application.payment_code.ilike(pattern),
            User.username.ilike(pattern),
            User.first_name.ilike(pattern),
            User.last_name.ilike(pattern),
        ]
        if raw.isdigit():
            conditions.append(Application.user_id == int(raw))
        stmt = stmt.join(User, User.id == Application.user_id).where(or_(*conditions))
    return stmt


def _with_relations(stmt):
    return stmt.options(
        selectinload(Application.raffle),
        selectinload(Application.user),
        selectinload(Application.organization),
        selectinload(Application.payment_method),
    )


async def get(session: AsyncSession, application_id: int) -> Application | None:
    stmt = _with_relations(select(Application).where(Application.id == application_id))
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_public_id(session: AsyncSession, public_id: str) -> Application | None:
    stmt = _with_relations(select(Application).where(Application.public_id == public_id))
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_payment_code(
    session: AsyncSession, raffle_id: int, payment_code: str
) -> Application | None:
    stmt = _with_relations(
        select(Application).where(
            Application.raffle_id == raffle_id,
            Application.payment_code == payment_code,
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_locked(session: AsyncSession, application_id: int) -> Application | None:
    """Lock the application row (first lock in the financial lock order)."""
    stmt = _with_relations(
        select(Application).where(Application.id == application_id)
    ).with_for_update(of=Application)
    return (await session.execute(stmt)).scalar_one_or_none()


async def create(session: AsyncSession, **values) -> Application:
    application = Application(**values)
    session.add(application)
    await session.flush()
    await session.refresh(application)
    return application


async def payment_code_exists(session: AsyncSession, raffle_id: int, payment_code: str) -> bool:
    stmt = select(Application.id).where(
        Application.raffle_id == raffle_id,
        Application.payment_code == payment_code,
    )
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def latest_draft(session: AsyncSession, raffle_id: int, user_id: int) -> Application | None:
    stmt = _with_relations(
        select(Application)
        .where(
            Application.raffle_id == raffle_id,
            Application.user_id == user_id,
            Application.status.in_([ApplicationStatus.DRAFT, ApplicationStatus.AWAITING_RECEIPT]),
        )
        .order_by(Application.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def open_for_user(session: AsyncSession, raffle_id: int, user_id: int) -> list[Application]:
    stmt = _with_relations(
        select(Application)
        .where(
            Application.raffle_id == raffle_id,
            Application.user_id == user_id,
            Application.status.in_(OPEN_STATUSES),
        )
        .order_by(Application.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def for_user(session: AsyncSession, raffle_id: int, user_id: int) -> list[Application]:
    stmt = _with_relations(
        select(Application)
        .where(Application.raffle_id == raffle_id, Application.user_id == user_id)
        .order_by(Application.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def list_filtered(
    session: AsyncSession,
    filters: ApplicationFilters,
    *,
    limit: int = 10,
    offset: int = 0,
) -> list[Application]:
    stmt = _with_relations(select(Application))
    stmt = _apply_filters(stmt, filters)
    stmt = stmt.order_by(Application.created_at.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars().unique())


async def count_filtered(session: AsyncSession, filters: ApplicationFilters) -> int:
    stmt = select(func.count(func.distinct(Application.id))).select_from(Application)
    stmt = _apply_filters(stmt, filters)
    return int((await session.execute(stmt)).scalar_one())


async def confirmed_ranges(
    session: AsyncSession, raffle_id: int, user_id: int
) -> list[tuple[int, int]]:
    stmt = (
        select(Application.first_ticket_number, Application.last_ticket_number)
        .where(
            Application.raffle_id == raffle_id,
            Application.user_id == user_id,
            Application.status == ApplicationStatus.CONFIRMED,
            Application.first_ticket_number.is_not(None),
            Application.last_ticket_number.is_not(None),
        )
        .order_by(Application.first_ticket_number)
    )
    rows = (await session.execute(stmt)).all()
    return [(int(first), int(last)) for first, last in rows]


async def all_confirmed(session: AsyncSession, raffle_id: int) -> list[Application]:
    stmt = _with_relations(
        select(Application)
        .where(
            Application.raffle_id == raffle_id,
            Application.status == ApplicationStatus.CONFIRMED,
        )
        .order_by(Application.first_ticket_number)
    )
    return list((await session.execute(stmt)).scalars().unique())


async def count_by_status(session: AsyncSession, raffle_id: int) -> dict[ApplicationStatus, int]:
    stmt = (
        select(Application.status, func.count())
        .where(Application.raffle_id == raffle_id)
        .group_by(Application.status)
    )
    return {status: int(count) for status, count in (await session.execute(stmt)).all()}


async def pending_for_raffle(session: AsyncSession, raffle_id: int) -> list[Application]:
    stmt = _with_relations(
        select(Application)
        .where(
            Application.raffle_id == raffle_id,
            Application.status.in_(
                [ApplicationStatus.PENDING_REVIEW, ApplicationStatus.REVISION_REQUESTED]
            ),
        )
        .order_by(Application.submitted_at)
    )
    return list((await session.execute(stmt)).scalars().unique())


async def drafts_older_than(session: AsyncSession, cutoff: datetime) -> list[Application]:
    stmt = select(Application).where(
        Application.status.in_([ApplicationStatus.DRAFT, ApplicationStatus.AWAITING_RECEIPT]),
        Application.created_at < cutoff,
    )
    return list((await session.execute(stmt)).scalars())


async def duplicate_receipt(
    session: AsyncSession, file_unique_id: str, *, exclude_id: int | None = None
) -> Application | None:
    stmt = select(Application).where(Application.receipt_file_unique_id == file_unique_id)
    if exclude_id is not None:
        stmt = stmt.where(Application.id != exclude_id)
    return (await session.execute(stmt.limit(1))).scalar_one_or_none()


async def recent_confirmed_for_raffle(
    session: AsyncSession, raffle_id: int, *, limit: int = 5
) -> list[Application]:
    stmt = _with_relations(
        select(Application)
        .where(
            Application.raffle_id == raffle_id,
            Application.status == ApplicationStatus.CONFIRMED,
        )
        .order_by(Application.reviewed_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().unique())
