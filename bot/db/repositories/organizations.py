"""Organization and payment method repositories."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.models import Application, Organization, PaymentMethod


async def get(session: AsyncSession, organization_id: int) -> Organization | None:
    return await session.get(Organization, organization_id)


async def get_by_slug(session: AsyncSession, slug: str) -> Organization | None:
    return (
        await session.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()


async def list_all(session: AsyncSession, *, only_active: bool = False) -> list[Organization]:
    stmt = select(Organization).options(selectinload(Organization.payment_methods))
    if only_active:
        stmt = stmt.where(Organization.is_active.is_(True))
    stmt = stmt.order_by(Organization.name)
    return list((await session.execute(stmt)).scalars().unique())


async def create(session: AsyncSession, **values) -> Organization:
    organization = Organization(**values)
    session.add(organization)
    await session.flush()
    return organization


async def load_payment_methods(
    session: AsyncSession, organization_id: int, *, only_active: bool = False
) -> list[PaymentMethod]:
    stmt = select(PaymentMethod).where(PaymentMethod.organization_id == organization_id)
    if only_active:
        stmt = stmt.where(PaymentMethod.is_active.is_(True))
    stmt = stmt.order_by(PaymentMethod.sort_order, PaymentMethod.id)
    return list((await session.execute(stmt)).scalars())


async def get_payment_method(session: AsyncSession, method_id: int) -> PaymentMethod | None:
    return await session.get(PaymentMethod, method_id)


async def create_payment_method(session: AsyncSession, **values) -> PaymentMethod:
    method = PaymentMethod(**values)
    session.add(method)
    await session.flush()
    return method


async def count_applications(session: AsyncSession, organization_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(Application)
        .where(Application.organization_id == organization_id)
    )
    return int((await session.execute(stmt)).scalar_one())


async def has_active_payment_methods(session: AsyncSession, organization_id: int) -> bool:
    stmt = (
        select(PaymentMethod.id)
        .where(
            PaymentMethod.organization_id == organization_id,
            PaymentMethod.is_active.is_(True),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def next_method_sort_order(session: AsyncSession, organization_id: int) -> int:
    stmt = select(func.coalesce(func.max(PaymentMethod.sort_order), 0)).where(
        PaymentMethod.organization_id == organization_id
    )
    return int((await session.execute(stmt)).scalar_one()) + 10
