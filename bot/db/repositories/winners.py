"""Winners repository."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Winner


async def count_for_raffle(session: AsyncSession, raffle_id: int) -> int:
    stmt = select(func.count()).select_from(Winner).where(Winner.raffle_id == raffle_id)
    return int((await session.execute(stmt)).scalar_one())


async def list_for_raffle(session: AsyncSession, raffle_id: int) -> list[Winner]:
    stmt = (
        select(Winner)
        .where(Winner.raffle_id == raffle_id)
        .options(selectinload(Winner.application))
        .order_by(Winner.place)
    )
    return list((await session.execute(stmt)).scalars())


async def get_by_place(session: AsyncSession, raffle_id: int, place: int) -> Winner | None:
    stmt = select(Winner).where(Winner.raffle_id == raffle_id, Winner.place == place)
    return (await session.execute(stmt)).scalar_one_or_none()


async def create(session: AsyncSession, **values) -> Winner:
    winner = Winner(**values)
    session.add(winner)
    await session.flush()
    return winner


async def delete_all(session: AsyncSession, raffle_id: int) -> None:
    for winner in await list_for_raffle(session, raffle_id):
        await session.delete(winner)
    await session.flush()
