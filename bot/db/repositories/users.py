"""User repository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User


async def get(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def upsert(
    session: AsyncSession,
    *,
    user_id: int,
    username: str | None,
    first_name: str,
    last_name: str | None,
    language_code: str | None,
    seen_at: datetime,
) -> User:
    stmt = (
        insert(User)
        .values(
            id=user_id,
            username=username,
            first_name=first_name or "",
            last_name=last_name,
            language_code=language_code,
            created_at=seen_at,
            last_seen_at=seen_at,
        )
        .on_conflict_do_update(
            index_elements=[User.id],
            set_={
                "username": username,
                "first_name": first_name or "",
                "last_name": last_name,
                "language_code": language_code,
                "last_seen_at": seen_at,
            },
        )
        .returning(User.id)
    )
    await session.execute(stmt)
    user = await session.get(User, user_id)
    if user is None:  # pragma: no cover - defensive
        raise RuntimeError("user upsert failed")
    return user


async def record_consent(
    session: AsyncSession,
    user: User,
    *,
    rules_version: str,
    privacy_version: str,
    accepted_at: datetime,
    age_required: bool,
    publish_name: bool | None,
) -> None:
    user.rules_accepted_at = accepted_at
    user.rules_version = rules_version
    user.privacy_accepted_at = accepted_at
    user.privacy_version = privacy_version
    if age_required:
        user.age_confirmed_at = accepted_at
    if publish_name is not None:
        user.publish_name_consent_at = accepted_at if publish_name else None
    await session.flush()


async def set_publish_name_consent(
    session: AsyncSession, user_id: int, *, consent: bool, at: datetime
) -> None:
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(publish_name_consent_at=at if consent else None)
    )


async def search(session: AsyncSession, query: str, *, limit: int = 20) -> list[User]:
    raw = query.strip()
    pattern = f"%{raw}%"
    stmt = select(User).where(
        User.username.ilike(pattern)
        | User.first_name.ilike(pattern)
        | User.last_name.ilike(pattern)
    )
    if raw.isdigit():
        stmt = select(User).where(
            (User.id == int(raw))
            | User.username.ilike(pattern)
            | User.first_name.ilike(pattern)
            | User.last_name.ilike(pattern)
        )
    stmt = stmt.order_by(User.last_seen_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().unique())


async def count(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count()).select_from(User))).scalar_one())
