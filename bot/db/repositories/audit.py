"""Audit log repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import AuditLog


async def log(
    session: AsyncSession,
    *,
    entity_type: str,
    action: str,
    actor_id: int | None = None,
    entity_id: str | int | None = None,
    application_id: int | None = None,
    raffle_id: int | None = None,
    old_status: str | None = None,
    new_status: str | None = None,
    payload: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> AuditLog:
    entry = AuditLog(
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        application_id=application_id,
        raffle_id=raffle_id,
        actor_id=actor_id,
        action=action,
        old_status=old_status,
        new_status=new_status,
        payload=payload,
    )
    if at is not None:
        entry.created_at = at
    session.add(entry)
    await session.flush()
    return entry


async def list_entries(
    session: AsyncSession,
    *,
    action: str | None = None,
    actor_id: int | None = None,
    since: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if since is not None:
        stmt = stmt.where(AuditLog.created_at >= since)
    stmt = stmt.limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars())
