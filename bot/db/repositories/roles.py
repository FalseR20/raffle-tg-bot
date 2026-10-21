"""Role repository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.enums import RoleName
from bot.db.models import Role


async def active_for_user(session: AsyncSession, user_id: int) -> list[Role]:
    stmt = (
        select(Role)
        .where(Role.user_id == user_id, Role.revoked_at.is_(None))
        .options(selectinload(Role.organization))
    )
    return list((await session.execute(stmt)).scalars().unique())


async def has_role(
    session: AsyncSession,
    user_id: int,
    role: RoleName,
    *,
    organization_id: int | None = None,
) -> bool:
    stmt = select(Role.id).where(
        Role.user_id == user_id, Role.role == role, Role.revoked_at.is_(None)
    )
    if organization_id is not None:
        stmt = stmt.where(Role.organization_id == organization_id)
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def active_holders(
    session: AsyncSession,
    role: RoleName,
    *,
    organization_id: int | None = None,
) -> list[Role]:
    stmt = select(Role).where(Role.role == role, Role.revoked_at.is_(None))
    if organization_id is not None:
        stmt = stmt.where(Role.organization_id == organization_id)
    return list((await session.execute(stmt)).scalars())


async def grant(
    session: AsyncSession,
    *,
    user_id: int,
    role: RoleName,
    organization_id: int | None,
    granted_by: int | None,
    at: datetime,
) -> Role:
    entry = Role(
        user_id=user_id,
        role=role,
        organization_id=organization_id,
        granted_by=granted_by,
        granted_at=at,
    )
    session.add(entry)
    await session.flush()
    return entry


async def revoke(
    session: AsyncSession,
    *,
    user_id: int,
    role: RoleName,
    organization_id: int | None,
    revoked_by: int,
    at: datetime,
) -> int:
    stmt = (
        update(Role)
        .where(
            Role.user_id == user_id,
            Role.role == role,
            Role.revoked_at.is_(None),
        )
        .values(revoked_at=at, revoked_by=revoked_by)
    )
    if organization_id is None:
        stmt = stmt.where(Role.organization_id.is_(None))
    else:
        stmt = stmt.where(Role.organization_id == organization_id)
    result = await session.execute(stmt)
    return int(result.rowcount or 0)


async def list_active(session: AsyncSession, *, role: RoleName | None = None) -> list[Role]:
    stmt = (
        select(Role)
        .where(Role.revoked_at.is_(None))
        .options(selectinload(Role.organization), selectinload(Role.user))
        .order_by(Role.granted_at.desc())
    )
    if role is not None:
        stmt = stmt.where(Role.role == role)
    return list((await session.execute(stmt)).scalars().unique())


async def active_organization_ids(session: AsyncSession, user_id: int) -> set[int]:
    stmt = select(Role.organization_id).where(
        Role.user_id == user_id,
        Role.role == RoleName.ORG_REP,
        Role.revoked_at.is_(None),
        Role.organization_id.is_not(None),
    )
    return {row for row in (await session.execute(stmt)).scalars() if row is not None}
