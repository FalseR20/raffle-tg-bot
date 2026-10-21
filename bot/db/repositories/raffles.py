"""Raffle repository."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.enums import RaffleStatus
from bot.db.models import (
    Application,
    Organization,
    Raffle,
    RaffleOrganization,
)


async def get(session: AsyncSession, raffle_id: int) -> Raffle | None:
    stmt = select(Raffle).where(Raffle.id == raffle_id).options(selectinload(Raffle.organizations))
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_slug(session: AsyncSession, slug: str) -> Raffle | None:
    return (await session.execute(select(Raffle).where(Raffle.slug == slug))).scalar_one_or_none()


async def get_current(session: AsyncSession) -> Raffle | None:
    stmt = select(Raffle).where(Raffle.status.in_([RaffleStatus.ACTIVE, RaffleStatus.CLOSED]))
    return (await session.execute(stmt)).scalar_one_or_none()


async def latest_results_for_user(session: AsyncSession, user_id: int) -> Raffle | None:
    """Latest finished/archived raffle in which the user participated."""
    stmt = (
        select(Raffle)
        .join(Application, Application.raffle_id == Raffle.id)
        .where(
            Application.user_id == user_id,
            Raffle.status.in_([RaffleStatus.FINISHED, RaffleStatus.ARCHIVED]),
        )
        .order_by(Raffle.finished_at.desc().nulls_last(), Raffle.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def latest_scheduled(session: AsyncSession) -> Raffle | None:
    stmt = (
        select(Raffle)
        .where(Raffle.status == RaffleStatus.SCHEDULED)
        .order_by(Raffle.start_at)
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_all(
    session: AsyncSession, *, status: RaffleStatus | None = None, limit: int = 20
) -> list[Raffle]:
    stmt = select(Raffle).order_by(Raffle.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Raffle.status == status)
    return list((await session.execute(stmt)).scalars())


async def create(session: AsyncSession, **values) -> Raffle:
    raffle = Raffle(**values)
    session.add(raffle)
    await session.flush()
    return raffle


async def set_organizations(
    session: AsyncSession, raffle_id: int, organization_ids: list[int]
) -> None:
    await session.execute(
        delete(RaffleOrganization).where(RaffleOrganization.raffle_id == raffle_id)
    )
    for order, organization_id in enumerate(organization_ids):
        session.add(
            RaffleOrganization(
                raffle_id=raffle_id,
                organization_id=organization_id,
                is_enabled=True,
                sort_order=order * 10,
            )
        )
    await session.flush()


async def enabled_organizations(session: AsyncSession, raffle_id: int) -> list[Organization]:
    stmt = (
        select(Organization)
        .join(RaffleOrganization, RaffleOrganization.organization_id == Organization.id)
        .where(
            RaffleOrganization.raffle_id == raffle_id,
            RaffleOrganization.is_enabled.is_(True),
            Organization.is_active.is_(True),
        )
        .options(selectinload(Organization.payment_methods))
        .order_by(RaffleOrganization.sort_order, Organization.name)
    )
    return list((await session.execute(stmt)).scalars().unique())


async def is_organization_enabled(
    session: AsyncSession, raffle_id: int, organization_id: int
) -> bool:
    stmt = (
        select(RaffleOrganization.organization_id)
        .where(
            RaffleOrganization.raffle_id == raffle_id,
            RaffleOrganization.organization_id == organization_id,
            RaffleOrganization.is_enabled.is_(True),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def count_applications(session: AsyncSession, raffle_id: int) -> int:
    stmt = select(func.count()).select_from(Application).where(Application.raffle_id == raffle_id)
    return int((await session.execute(stmt)).scalar_one())


async def lock(session: AsyncSession, raffle_id: int) -> Raffle | None:
    stmt = (
        select(Raffle)
        .where(Raffle.id == raffle_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def scheduled_due(session: AsyncSession) -> list[Raffle]:
    stmt = select(Raffle).where(Raffle.status == RaffleStatus.SCHEDULED)
    return list((await session.execute(stmt)).scalars())
