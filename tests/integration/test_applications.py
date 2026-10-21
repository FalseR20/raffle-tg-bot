"""Participant application flow integration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bot.db.enums import ApplicationStatus, RaffleStatus
from bot.db.session import session_scope
from bot.services import applications as applications_service
from bot.services.errors import (
    AlreadyProcessedError,
    InvalidStateError,
    RaffleNotAcceptingError,
    ValidationError,
)
from bot.services.permissions import Permissions
from bot.services.reviews import confirm_application
from tests.helpers import (
    make_application,
    make_organization,
    make_payment_method,
    make_raffle,
    make_user,
)

pytestmark = pytest.mark.integration
ADMIN = Permissions(user_id=100, is_admin=True)


async def test_draft_code_method_and_snapshot(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        method = await make_payment_method(session, org, title="Старая карта")
        raffle = await make_raffle(session, organizations=[org])
        application = await applications_service.start_draft(
            session, user=user, raffle=raffle, organization=org, now=datetime.now(UTC)
        )
        code = application.payment_code
        assert len(code) == 5
        assert set(code) <= set(applications_service.PAYMENT_CODE_ALPHABET)
        await applications_service.choose_payment_method(
            session, application=application, method=method, now=datetime.now(UTC)
        )
        method.title = "Новая карта"
        method.instructions_text = "Новые инструкции"
        await session.flush()
        assert application.payment_method_title == "Старая карта"
        assert application.payment_instructions_snapshot != "Новые инструкции"
    async with session_factory() as session:
        refreshed = await session.get(type(application), application.id)
        assert refreshed.payment_method_title == "Старая карта"
        assert refreshed.payment_code == code


async def test_only_one_draft_and_limit(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        await applications_service.start_draft(
            session, user=user, raffle=raffle, organization=org, now=datetime.now(UTC)
        )
        with pytest.raises(InvalidStateError):
            await applications_service.start_draft(
                session, user=user, raffle=raffle, organization=org, now=datetime.now(UTC)
            )
    async with session_scope(session_factory) as session:
        user = await make_user(session, 501)
        for _ in range(20):
            await make_application(
                session,
                raffle=raffle,
                user=user,
                organization=org,
                amount=Decimal("5"),
                status=ApplicationStatus.CANCELLED,
            )
        with pytest.raises(ValidationError):
            await applications_service.start_draft(
                session, user=user, raffle=raffle, organization=org, now=datetime.now(UTC)
            )


async def test_closed_raffle_rejects_new_draft(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org], status=RaffleStatus.CLOSED)
        with pytest.raises(RaffleNotAcceptingError):
            await applications_service.start_draft(
                session, user=user, raffle=raffle, organization=org, now=datetime.now(UTC)
            )


async def test_cancel_after_confirmation_fails_atomically(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        application = await make_application(
            session, raffle=raffle, user=user, organization=org, amount=Decimal("5")
        )
    async with session_scope(session_factory) as session:
        await confirm_application(
            session,
            application_id=application.id,
            reviewer_id=100,
            reviewer=ADMIN,
            now=datetime.now(UTC),
        )
    async with session_scope(session_factory) as session:
        application = await session.get(type(application), application.id)
        with pytest.raises(AlreadyProcessedError):
            await applications_service.cancel_by_user(
                session, application=application, user_id=user.id, now=datetime.now(UTC)
            )
    async with session_factory() as session:
        refreshed = await session.get(type(application), application.id)
        assert refreshed.status is ApplicationStatus.CONFIRMED


async def test_amount_validation_and_preview(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        application = await applications_service.start_draft(
            session, user=user, raffle=raffle, organization=org, now=datetime.now(UTC)
        )
        methods = await orgs_repo_methods(session, org)
        await applications_service.choose_payment_method(
            session, application=application, method=methods[0], now=datetime.now(UTC)
        )
        preview = await applications_service.preview_for(
            session,
            application=application,
            entered_amount=Decimal("9"),
            ticket_price=raffle.ticket_price,
        )
        assert preview.preview_tickets == 1
        with pytest.raises(ValidationError):
            await applications_service.set_amount(
                session,
                application=application,
                amount=Decimal("4"),
                now=datetime.now(UTC),
                min_amount=Decimal("5"),
                max_amount=Decimal("10000"),
            )
        await applications_service.set_amount(
            session,
            application=application,
            amount=Decimal("9"),
            now=datetime.now(UTC),
            min_amount=Decimal("5"),
            max_amount=Decimal("10000"),
        )
        assert application.status is ApplicationStatus.AWAITING_RECEIPT


async def orgs_repo_methods(session, org):
    from bot.db.repositories import organizations as orgs_repo

    return await orgs_repo.load_payment_methods(session, org.id, only_active=True)
