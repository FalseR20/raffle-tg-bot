from decimal import Decimal

import pytest

from bot.db.enums import ApplicationStatus, RaffleStatus
from bot.db.models import ParticipantTotal, Raffle
from bot.db.session import session_scope
from bot.services.errors import (
    AlreadyProcessedError,
    InvalidStateError,
    PermissionDeniedError,
    SelfReviewError,
)
from bot.services.permissions import Permissions
from bot.services.reviews import (
    confirm_application,
    reject_application,
    request_new_receipt,
    void_application,
)
from tests.helpers import (
    make_application,
    make_organization,
    make_raffle,
    make_user,
)

pytestmark = pytest.mark.integration

ADMIN = Permissions(user_id=100, is_admin=True)
ADMIN_2 = Permissions(user_id=101, is_admin=True)


async def test_9_plus_6_equals_three_tickets(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org_a = await make_organization(session, name="Организация A", chat_id=-1001)
        org_b = await make_organization(session, name="Организация B", chat_id=-1002)
        raffle = await make_raffle(session, organizations=[org_a, org_b])
        first = await make_application(
            session, raffle=raffle, user=user, organization=org_a, amount=Decimal("9")
        )
        second = await make_application(
            session, raffle=raffle, user=user, organization=org_b, amount=Decimal("6")
        )

    async with session_scope(session_factory) as session:
        result = await confirm_application(
            session,
            application_id=first.id,
            reviewer_id=100,
            reviewer=ADMIN,
            now=first.submitted_at,
        )
        assert result.range.first == 1 and result.range.last == 1
        assert result.total_tickets == 1

    async with session_scope(session_factory) as session:
        result = await confirm_application(
            session,
            application_id=second.id,
            reviewer_id=100,
            reviewer=ADMIN,
            now=second.submitted_at,
        )
        assert (result.range.first, result.range.last) == (2, 3)
        assert result.total_amount == Decimal("15.00")
        assert result.total_tickets == 3
        assert result.all_ranges == [(1, 1), (2, 3)]

    async with session_factory() as session:
        total = await session.get(ParticipantTotal, (raffle.id, user.id))
        assert total.confirmed_amount == Decimal("15.00")
        assert total.tickets_issued == 3
        refreshed = await session.get(Raffle, raffle.id)
        assert refreshed.last_ticket_number == 3


async def test_9_plus_5_equals_two_tickets(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        first = await make_application(
            session, raffle=raffle, user=user, organization=org, amount=Decimal("9")
        )
        second = await make_application(
            session, raffle=raffle, user=user, organization=org, amount=Decimal("5")
        )

    async with session_scope(session_factory) as session:
        await confirm_application(
            session,
            application_id=first.id,
            reviewer_id=100,
            reviewer=ADMIN,
            now=first.submitted_at,
        )
    async with session_scope(session_factory) as session:
        result = await confirm_application(
            session,
            application_id=second.id,
            reviewer_id=100,
            reviewer=ADMIN,
            now=second.submitted_at,
        )
        assert result.total_tickets == 2
        assert result.range.first == 2 and result.range.last == 2


async def test_remainders_of_different_users_are_not_pooled(session_factory):
    async with session_scope(session_factory) as session:
        user_a = await make_user(session, 501)
        user_b = await make_user(session, 502)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        app_a = await make_application(
            session, raffle=raffle, user=user_a, organization=org, amount=Decimal("9")
        )
        app_b = await make_application(
            session, raffle=raffle, user=user_b, organization=org, amount=Decimal("9")
        )

    async with session_scope(session_factory) as session:
        await confirm_application(
            session,
            application_id=app_a.id,
            reviewer_id=100,
            reviewer=ADMIN,
            now=app_a.submitted_at,
        )
    async with session_scope(session_factory) as session:
        await confirm_application(
            session,
            application_id=app_b.id,
            reviewer_id=100,
            reviewer=ADMIN,
            now=app_b.submitted_at,
        )
    async with session_factory() as session:
        raffle_row = await session.get(Raffle, raffle.id)
        assert raffle_row.last_ticket_number == 2


async def test_non_contiguous_participant_ranges(session_factory):
    async with session_scope(session_factory) as session:
        user_a = await make_user(session, 501)
        user_b = await make_user(session, 502)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        a1 = await make_application(
            session, raffle=raffle, user=user_a, organization=org, amount=Decimal("5")
        )
        b1 = await make_application(
            session, raffle=raffle, user=user_b, organization=org, amount=Decimal("5")
        )
        a2 = await make_application(
            session, raffle=raffle, user=user_a, organization=org, amount=Decimal("10")
        )

    for application in (a1, b1, a2):
        async with session_scope(session_factory) as session:
            await confirm_application(
                session,
                application_id=application.id,
                reviewer_id=100,
                reviewer=ADMIN,
                now=application.submitted_at,
            )
    async with session_factory() as session:
        total = await session.get(ParticipantTotal, (raffle.id, user_a.id))
        assert total.tickets_issued == 3
        from bot.db.repositories import applications as apps_repo

        assert await apps_repo.confirmed_ranges(session, raffle.id, user_a.id) == [(1, 1), (3, 4)]


async def test_confirmation_is_idempotent(session_factory):
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
            now=application.submitted_at,
        )
    async with session_scope(session_factory) as session:
        with pytest.raises(AlreadyProcessedError):
            await confirm_application(
                session,
                application_id=application.id,
                reviewer_id=100,
                reviewer=ADMIN,
                now=application.submitted_at,
            )


async def test_self_confirmation_is_forbidden(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        application = await make_application(
            session, raffle=raffle, user=user, organization=org, amount=Decimal("5")
        )

    participant_reviewer = Permissions(user_id=500, org_rep_ids=frozenset({org.id}))
    async with session_scope(session_factory) as session:
        with pytest.raises(SelfReviewError):
            await confirm_application(
                session,
                application_id=application.id,
                reviewer_id=500,
                reviewer=participant_reviewer,
                now=application.submitted_at,
            )


async def test_org_rep_cannot_confirm_another_organization(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org_a = await make_organization(session, name="Org A", chat_id=-1)
        org_b = await make_organization(session, name="Org B", chat_id=-2)
        raffle = await make_raffle(session, organizations=[org_a, org_b])
        application = await make_application(
            session, raffle=raffle, user=user, organization=org_a, amount=Decimal("5")
        )

    rep_b = Permissions(user_id=700, org_rep_ids=frozenset({org_b.id}))
    async with session_scope(session_factory) as session:
        with pytest.raises(PermissionDeniedError):
            await confirm_application(
                session,
                application_id=application.id,
                reviewer_id=700,
                reviewer=rep_b,
                now=application.submitted_at,
            )


async def test_rejection_consumes_no_numbers(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        application = await make_application(
            session, raffle=raffle, user=user, organization=org, amount=Decimal("5")
        )

    async with session_scope(session_factory) as session:
        await reject_application(
            session,
            application_id=application.id,
            reviewer_id=100,
            reviewer=ADMIN,
            reason="платёж не найден",
            now=application.submitted_at,
        )
    async with session_factory() as session:
        refreshed = await session.get(Raffle, raffle.id)
        assert refreshed.last_ticket_number == 0
        total = await session.get(ParticipantTotal, (raffle.id, user.id))
        assert total is None or total.tickets_issued == 0


async def test_revision_preserves_submitted_at_and_issues_no_tickets(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        application = await make_application(
            session, raffle=raffle, user=user, organization=org, amount=Decimal("5")
        )
        original_submitted = application.submitted_at

    async with session_scope(session_factory) as session:
        await request_new_receipt(
            session,
            application_id=application.id,
            reviewer_id=100,
            reviewer=ADMIN,
            reason="чек не читается",
            now=original_submitted,
        )
    async with session_factory() as session:
        refreshed = await session.get(type(application), application.id)
        assert refreshed.status is ApplicationStatus.REVISION_REQUESTED
        assert refreshed.submitted_at == original_submitted
        assert refreshed.tickets_awarded == 0
        raffle_row = await session.get(Raffle, raffle.id)
        assert raffle_row.last_ticket_number == 0


async def test_void_only_last_range_and_reuses_numbers(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        first = await make_application(
            session, raffle=raffle, user=user, organization=org, amount=Decimal("9")
        )
        second = await make_application(
            session, raffle=raffle, user=user, organization=org, amount=Decimal("6")
        )

    async with session_scope(session_factory) as session:
        await confirm_application(
            session,
            application_id=first.id,
            reviewer_id=100,
            reviewer=ADMIN,
            now=first.submitted_at,
        )
    async with session_scope(session_factory) as session:
        await confirm_application(
            session,
            application_id=second.id,
            reviewer_id=100,
            reviewer=ADMIN,
            now=second.submitted_at,
        )

    async with session_scope(session_factory) as session:
        with pytest.raises(InvalidStateError):
            await void_application(
                session,
                application_id=first.id,
                actor_id=100,
                actor=ADMIN,
                reason="ошибка",
                now=second.submitted_at,
            )
    async with session_scope(session_factory) as session:
        result = await void_application(
            session,
            application_id=second.id,
            actor_id=100,
            actor=ADMIN,
            reason="ошибка",
            now=second.submitted_at,
        )
        assert (result.voided_range.first, result.voided_range.last) == (2, 3)
        assert result.total_amount == Decimal("9.00")
        assert result.total_tickets == 1
        assert result.raffle.last_ticket_number == 1

    async with session_factory() as session:
        refreshed = await session.get(type(second), second.id)
        assert refreshed.status is ApplicationStatus.VOIDED
        assert refreshed.first_ticket_number is None
        assert refreshed.voided_first_ticket_number == 2
        assert refreshed.voided_last_ticket_number == 3

    async with session_scope(session_factory) as session:
        third = await make_application(
            session, raffle=raffle, user=user, organization=org, amount=Decimal("5")
        )
    async with session_scope(session_factory) as session:
        result = await confirm_application(
            session,
            application_id=third.id,
            reviewer_id=100,
            reviewer=ADMIN,
            now=third.submitted_at,
        )
        assert (result.range.first, result.range.last) == (2, 2)


async def test_void_forbidden_after_finish(session_factory):
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
            now=application.submitted_at,
        )
        raffle_row = await session.get(Raffle, raffle.id)
        raffle_row.status = RaffleStatus.FINISHED
    async with session_scope(session_factory) as session:
        with pytest.raises(InvalidStateError):
            await void_application(
                session,
                application_id=application.id,
                actor_id=100,
                actor=ADMIN,
                reason="ошибка",
                now=application.submitted_at,
            )
