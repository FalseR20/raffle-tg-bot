"""Invite repository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Invite


async def create(session: AsyncSession, **values) -> Invite:
    invite = Invite(**values)
    session.add(invite)
    await session.flush()
    return invite


async def get_by_hash(session: AsyncSession, token_hash: str) -> Invite | None:
    stmt = (
        select(Invite)
        .where(Invite.token_hash == token_hash)
        .options(selectinload(Invite.organization))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_hash_locked(session: AsyncSession, token_hash: str) -> Invite | None:
    stmt = (
        select(Invite)
        .where(Invite.token_hash == token_hash)
        .with_for_update(of=Invite)
        .options(selectinload(Invite.organization))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get(session: AsyncSession, invite_id: int) -> Invite | None:
    stmt = select(Invite).where(Invite.id == invite_id).options(selectinload(Invite.organization))
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_active(session: AsyncSession, now: datetime) -> list[Invite]:
    stmt = (
        select(Invite)
        .where(
            Invite.used_at.is_(None),
            Invite.revoked_at.is_(None),
            Invite.expires_at > now,
        )
        .options(selectinload(Invite.organization))
        .order_by(Invite.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def list_all(session: AsyncSession, *, limit: int = 30) -> list[Invite]:
    stmt = (
        select(Invite)
        .options(selectinload(Invite.organization))
        .order_by(Invite.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())
