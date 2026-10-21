"""Settings repository (database overrides of environment defaults)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Setting


async def all_values(session: AsyncSession) -> dict[str, Any]:
    stmt = select(Setting)
    return {row.key: row.value for row in (await session.execute(stmt)).scalars()}


async def get(session: AsyncSession, key: str) -> Any | None:
    row = await session.get(Setting, key)
    return row.value if row else None


async def set_value(session: AsyncSession, key: str, value: Any, *, updated_by: int | None) -> None:
    stmt = (
        insert(Setting)
        .values(key=key, value=value, updated_by=updated_by)
        .on_conflict_do_update(
            index_elements=[Setting.key],
            set_={"value": value, "updated_by": updated_by},
        )
    )
    await session.execute(stmt)
