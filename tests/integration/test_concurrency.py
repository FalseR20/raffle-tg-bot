"""Concurrency invariants for financial operations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from bot.db.enums import ApplicationStatus
from bot.db.models import Application, ParticipantTotal, Raffle
from bot.db.repositories import applications as apps_repo
from bot.db.session import session_scope
from bot.services.errors import AlreadyProcessedError
from bot.services.permissions import Permissions
from bot.services.reviews import confirm_application
from tests.helpers import make_application, make_organization, make_raffle, make_user

pytestmark = pytest.mark.integration

ADMIN = Permissions(user_id=100, is_admin=True)
ADMIN_2 = Permissions(user_id=101, is_admin=True)


async def _confirm(factory, application_id: int, reviewer: Permissions):
    async with session_scope(factory) as session:
        return await confirm_application(
            session,
            application_id=application_id,
            reviewer_id=reviewer.user_id,
            reviewer=reviewer,
            now=datetime.now(UTC),
        )


async def test_two_reviewers_confirm_same_application_once(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        application = await make_application(
            session, raffle=raffle, user=user, organization=org, amount=Decimal("5")
        )

    results = await asyncio.gather(
        _confirm(session_factory, application.id, ADMIN),
        _confirm(session_factory, application.id, ADMIN_2),
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], AlreadyProcessedError)

    async with session_factory() as session:
        raffle_row = await session.get(Raffle, raffle.id)
        assert raffle_row.last_ticket_number == 1
        total = await session.get(ParticipantTotal, (raffle.id, user.id))
        assert total.tickets_issued == 1
        confirmed = (
            (
                await session.execute(
                    select(Application).where(Application.status == ApplicationStatus.CONFIRMED)
                )
            )
            .scalars()
            .all()
        )
        assert len(confirmed) == 1


async def test_two_reviewers_with_preloaded_application_confirm_once(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        application = await make_application(
            session, raffle=raffle, user=user, organization=org, amount=Decimal("5")
        )

    barrier = asyncio.Barrier(2)

    async def preload_and_confirm(reviewer: Permissions):
        async with session_factory() as session:
            # This mirrors the real callback flow: the handler loads the
            # application before the review service acquires the row lock.
            preloaded = await apps_repo.get(session, application.id)
            assert preloaded is not None
            assert preloaded.status is ApplicationStatus.PENDING_REVIEW
            await barrier.wait()
            try:
                result = await confirm_application(
                    session,
                    application_id=application.id,
                    reviewer_id=reviewer.user_id,
                    reviewer=reviewer,
                    now=datetime.now(UTC),
                )
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise

    results = await asyncio.gather(
        preload_and_confirm(ADMIN),
        preload_and_confirm(ADMIN_2),
        return_exceptions=True,
    )
    successes = [result for result in results if not isinstance(result, Exception)]
    errors = [result for result in results if isinstance(result, Exception)]
    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], AlreadyProcessedError)

    async with session_factory() as session:
        raffle_row = await session.get(Raffle, raffle.id)
        assert raffle_row.last_ticket_number == 1
        total = await session.get(ParticipantTotal, (raffle.id, user.id))
        assert total.tickets_issued == 1
        confirmed = (
            await session.execute(select(Application).where(Application.id == application.id))
        ).scalar_one()
        assert confirmed.status is ApplicationStatus.CONFIRMED
        assert confirmed.first_ticket_number == 1
        assert confirmed.last_ticket_number == 1


async def test_concurrent_same_user_payments_in_different_organizations(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org_a = await make_organization(session, name="Org A", chat_id=-1)
        org_b = await make_organization(session, name="Org B", chat_id=-2)
        raffle = await make_raffle(session, organizations=[org_a, org_b])
        first = await make_application(
            session, raffle=raffle, user=user, organization=org_a, amount=Decimal("9")
        )
        second = await make_application(
            session, raffle=raffle, user=user, organization=org_b, amount=Decimal("6")
        )

    results = await asyncio.gather(
        _confirm(session_factory, first.id, ADMIN),
        _confirm(session_factory, second.id, ADMIN_2),
        return_exceptions=True,
    )
    assert not [r for r in results if isinstance(r, Exception)], results

    async with session_factory() as session:
        total = await session.get(ParticipantTotal, (raffle.id, user.id))
        assert total.confirmed_amount == Decimal("15.00")
        assert total.tickets_issued == 3
        raffle_row = await session.get(Raffle, raffle.id)
        assert raffle_row.last_ticket_number == 3
        ranges = await apps_repo.confirmed_ranges(session, raffle.id, user.id)
        assert sorted(ranges) == [(1, 1), (2, 3)]


async def test_concurrent_different_users_get_contiguous_ranges(session_factory):
    async with session_scope(session_factory) as session:
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        applications = []
        for user_id in range(600, 610):
            user = await make_user(session, user_id)
            applications.append(
                await make_application(
                    session, raffle=raffle, user=user, organization=org, amount=Decimal("5")
                )
            )

    results = await asyncio.gather(
        *(_confirm(session_factory, app.id, ADMIN) for app in applications),
        return_exceptions=True,
    )
    assert not [r for r in results if isinstance(r, Exception)], results

    async with session_factory() as session:
        raffle_row = await session.get(Raffle, raffle.id)
        assert raffle_row.last_ticket_number == len(applications)
        rows = (
            await session.execute(
                select(Application.first_ticket_number, Application.last_ticket_number)
                .where(Application.status == ApplicationStatus.CONFIRMED)
                .order_by(Application.first_ticket_number)
            )
        ).all()
        assert [int(first) for first, _ in rows] == list(range(1, len(applications) + 1))
        assert all(first == last for first, last in rows)
