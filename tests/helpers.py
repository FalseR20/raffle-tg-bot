"""Factories for integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.enums import ApplicationStatus, RaffleStatus
from bot.db.models import Application, Organization, PaymentMethod, Raffle, User
from bot.db.repositories import applications as apps_repo
from bot.db.repositories import organizations as orgs_repo
from bot.db.repositories import participants as participants_repo
from bot.db.repositories import raffles as raffles_repo
from bot.db.repositories import users as users_repo
from bot.services.applications import generate_payment_code
from bot.utils.slug import slugify


async def make_user(
    session: AsyncSession,
    user_id: int,
    *,
    username: str | None = None,
    first_name: str = "Иван",
    last_name: str | None = "Петров",
) -> User:
    return await users_repo.upsert(
        session,
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        language_code="ru",
        seen_at=datetime.now(UTC),
    )


async def make_organization(
    session: AsyncSession,
    *,
    name: str = "Организация A",
    chat_id: int = -1001234567890,
    with_method: bool = True,
) -> Organization:
    organization = await orgs_repo.create(
        session,
        slug=slugify(name),
        name=name,
        description="Помогаем людям",
        review_chat_id=chat_id,
        review_thread_id=None,
        contact_url="https://example.org",
        is_active=True,
    )
    if with_method:
        await make_payment_method(session, organization)
    return organization


async def make_payment_method(
    session: AsyncSession,
    organization: Organization,
    *,
    title: str = "Карта BYN",
    active: bool = True,
) -> PaymentMethod:
    return await orgs_repo.create_payment_method(
        session,
        organization_id=organization.id,
        title=title,
        instructions_text="Переведите на карту:\n`1234 5678 9012 3456`\n\n*Получатель:* Иван",
        is_active=active,
        sort_order=10,
    )


async def make_raffle(
    session: AsyncSession,
    *,
    organizations: list[Organization],
    title: str = "Осень 2026",
    ticket_price: Decimal = Decimal("5.00"),
    min_amount: Decimal | None = None,
    prizes_count: int = 3,
    status: RaffleStatus = RaffleStatus.ACTIVE,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    admin_id: int = 100,
    last_ticket_number: int = 0,
) -> Raffle:
    now = datetime.now(UTC)
    raffle = await raffles_repo.create(
        session,
        slug=slugify(title),
        title=title,
        description="Благотворительный розыгрыш",
        ticket_price=ticket_price,
        min_amount=min_amount or ticket_price,
        prizes_count=prizes_count,
        currency="BYN",
        start_at=start_at or now - timedelta(days=1),
        end_at=end_at or now + timedelta(days=30),
        status=status,
        channel_url="https://t.me/test_channel",
        channel_title="Тестовый канал",
        last_ticket_number=last_ticket_number,
        created_by=admin_id,
    )
    await raffles_repo.set_organizations(session, raffle.id, [o.id for o in organizations])
    return raffle


async def make_application(
    session: AsyncSession,
    *,
    raffle: Raffle,
    user: User,
    organization: Organization,
    amount: Decimal,
    method: PaymentMethod | None = None,
    status: ApplicationStatus = ApplicationStatus.PENDING_REVIEW,
    submitted_at: datetime | None = None,
    payment_code: str | None = None,
    with_receipt: bool = True,
) -> Application:
    if method is None:
        methods = await orgs_repo.load_payment_methods(session, organization.id, only_active=True)
        method = methods[0]
    now = datetime.now(UTC)
    application = await apps_repo.create(
        session,
        raffle_id=raffle.id,
        user_id=user.id,
        organization_id=organization.id,
        payment_method_id=method.id,
        payment_method_title=method.title,
        payment_instructions_snapshot=method.instructions_text,
        payment_code=payment_code or await generate_payment_code(session, raffle.id),
        amount=amount,
        currency="BYN",
        status=status,
        tickets_awarded=0,
        submitted_at=submitted_at or (now if status is ApplicationStatus.PENDING_REVIEW else None),
        receipt_type="pdf" if with_receipt else None,
        receipt_file_id=f"file-{user.id}-{amount}" if with_receipt else None,
        receipt_file_unique_id=f"uniq-{user.id}-{amount}-{now.timestamp()}"
        if with_receipt
        else None,
        receipt_object_key=f"receipts/{raffle.id}/{user.id}.pdf" if with_receipt else None,
        receipt_mime="application/pdf" if with_receipt else None,
        receipt_size=100 if with_receipt else None,
    )
    await participants_repo.ensure(session, raffle.id, user.id)
    return application
