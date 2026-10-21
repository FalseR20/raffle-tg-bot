"""Participant totals repository."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import ParticipantTotal


async def ensure(session: AsyncSession, raffle_id: int, user_id: int) -> None:
    """Create the totals row if missing without taking a row lock."""
    await session.execute(
        insert(ParticipantTotal)
        .values(
            raffle_id=raffle_id,
            user_id=user_id,
            confirmed_amount=Decimal("0.00"),
            tickets_issued=0,
        )
        .on_conflict_do_nothing(
            index_elements=[ParticipantTotal.raffle_id, ParticipantTotal.user_id]
        )
    )


async def get(session: AsyncSession, raffle_id: int, user_id: int) -> ParticipantTotal | None:
    stmt = (
        select(ParticipantTotal)
        .where(
            ParticipantTotal.raffle_id == raffle_id,
            ParticipantTotal.user_id == user_id,
        )
        .options(selectinload(ParticipantTotal.user))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_locked(session: AsyncSession, raffle_id: int, user_id: int) -> ParticipantTotal:
    """Lock the participant total row (second lock in the financial lock order)."""
    await ensure(session, raffle_id, user_id)
    stmt = (
        select(ParticipantTotal)
        .where(
            ParticipantTotal.raffle_id == raffle_id,
            ParticipantTotal.user_id == user_id,
        )
        .with_for_update()
        .options(selectinload(ParticipantTotal.user))
        .execution_options(populate_existing=True)
    )
    total = (await session.execute(stmt)).scalar_one()
    return total


async def list_for_raffle(
    session: AsyncSession, raffle_id: int, *, limit: int = 100, offset: int = 0
) -> list[ParticipantTotal]:
    stmt = (
        select(ParticipantTotal)
        .where(ParticipantTotal.raffle_id == raffle_id)
        .options(selectinload(ParticipantTotal.user))
        .order_by(ParticipantTotal.tickets_issued.desc(), ParticipantTotal.confirmed_amount.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars())


async def count_for_raffle(session: AsyncSession, raffle_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(ParticipantTotal)
        .where(ParticipantTotal.raffle_id == raffle_id)
    )
    return int((await session.execute(stmt)).scalar_one())


async def sum_tickets(session: AsyncSession, raffle_id: int) -> int:
    stmt = select(func.coalesce(func.sum(ParticipantTotal.tickets_issued), 0)).where(
        ParticipantTotal.raffle_id == raffle_id
    )
    return int((await session.execute(stmt)).scalar_one())


async def sum_amount(session: AsyncSession, raffle_id: int) -> Decimal:
    stmt = select(func.coalesce(func.sum(ParticipantTotal.confirmed_amount), 0)).where(
        ParticipantTotal.raffle_id == raffle_id
    )
    return Decimal((await session.execute(stmt)).scalar_one())
