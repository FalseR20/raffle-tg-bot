"""Lifecycle and raffle rules integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bot.db.enums import ApplicationStatus, RaffleStatus
from bot.db.repositories import raffles as raffles_repo
from bot.db.session import session_scope
from bot.services.errors import ExportError, InvalidStateError, ValidationError
from bot.services.exports import build_tickets_pdf
from bot.services.lifecycle import purge_cancelled_applications
from bot.services.permissions import Permissions
from bot.services.raffles import (
    RaffleInput,
    activate,
    duplicate,
    finish,
    process_schedule,
    stop_acceptance,
    validate_input,
)
from tests.helpers import (
    make_application,
    make_organization,
    make_raffle,
    make_user,
)

pytestmark = pytest.mark.integration
ADMIN = Permissions(user_id=100, is_admin=True)


class RecordingStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


async def test_legacy_cancelled_applications_are_purged_with_receipts(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        application = await make_application(
            session,
            raffle=raffle,
            user=user,
            organization=org,
            amount=Decimal("5"),
            status=ApplicationStatus.CANCELLED,
        )
        application_id = application.id
        key = application.receipt_object_key

    storage = RecordingStorage()
    async with session_scope(session_factory) as session:
        assert await purge_cancelled_applications(session, storage) == 1
    async with session_factory() as session:
        assert await session.get(type(application), application_id) is None
    assert storage.deleted == [key]


async def test_non_archived_raffles_are_listed_for_admin_shortcuts(session_factory):
    async with session_scope(session_factory) as session:
        org = await make_organization(session)
        active = await make_raffle(session, organizations=[org], title="Active")
        finished = await make_raffle(
            session, organizations=[org], title="Finished", status=RaffleStatus.FINISHED
        )
        archived = await make_raffle(
            session, organizations=[org], title="Archived", status=RaffleStatus.ARCHIVED
        )

    async with session_factory() as session:
        raffles = await raffles_repo.list_non_archived(session)
        ids = [raffle.id for raffle in raffles]
        assert ids[0] == active.id
        assert finished.id in ids
        assert archived.id not in ids


async def test_only_one_current_raffle(session_factory):
    async with session_scope(session_factory) as session:
        org = await make_organization(session)
        first = await make_raffle(session, organizations=[org], title="First")
        second = await make_raffle(
            session, organizations=[org], title="Second", status=RaffleStatus.DRAFT
        )
    async with session_scope(session_factory) as session:
        second = await session.get(type(second), second.id)
        with pytest.raises(ValidationError):
            await activate(session, actor=ADMIN, raffle=second, now=datetime.now(UTC))
    async with session_factory() as session:
        assert (await session.get(type(first), first.id)).status is RaffleStatus.ACTIVE


async def test_deadline_is_applied_on_schedule_tick(session_factory):
    async with session_scope(session_factory) as session:
        org = await make_organization(session)
        raffle = await make_raffle(
            session,
            organizations=[org],
            start_at=datetime.now(UTC) - timedelta(days=2),
            end_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    async with session_scope(session_factory) as session:
        activated, closed, _ = await process_schedule(session, now=datetime.now(UTC))
        assert not activated
        assert [item.id for item in closed] == [raffle.id]
    async with session_factory() as session:
        refreshed = await session.get(type(raffle), raffle.id)
        assert refreshed.status is RaffleStatus.CLOSED


async def test_scheduled_raffle_starts_and_respects_current(session_factory):
    async with session_scope(session_factory) as session:
        org = await make_organization(session)
        current = await make_raffle(session, organizations=[org], title="Current")
        scheduled = await make_raffle(
            session,
            organizations=[org],
            title="Scheduled",
            status=RaffleStatus.SCHEDULED,
            start_at=datetime.now(UTC) - timedelta(minutes=1),
            end_at=datetime.now(UTC) + timedelta(days=1),
        )
    async with session_scope(session_factory) as session:
        activated, _, _ = await process_schedule(session, now=datetime.now(UTC))
        assert not activated
    async with session_scope(session_factory) as session:
        current_row = await session.get(type(current), current.id)
        current_row.status = RaffleStatus.FINISHED
    async with session_scope(session_factory) as session:
        activated, _, _ = await process_schedule(session, now=datetime.now(UTC))
        assert [item.id for item in activated] == [scheduled.id]


async def test_drafts_expire_but_submitted_applications_do_not(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        draft = await make_application(
            session,
            raffle=raffle,
            user=user,
            organization=org,
            amount=Decimal("5"),
            status=ApplicationStatus.AWAITING_RECEIPT,
        )
        draft.created_at = datetime.now(UTC) - timedelta(hours=25)
        pending = await make_application(
            session,
            raffle=raffle,
            user=user,
            organization=org,
            amount=Decimal("5"),
            status=ApplicationStatus.PENDING_REVIEW,
        )
        pending.created_at = datetime.now(UTC) - timedelta(hours=48)
    async with session_scope(session_factory) as session:
        from bot.services.applications import expire_drafts

        expired = await expire_drafts(session, now=datetime.now(UTC))
        assert [app.id for app in expired] == [draft.id]
    async with session_factory() as session:
        assert (await session.get(type(draft), draft.id)).status is ApplicationStatus.EXPIRED
        assert (await session.get(type(pending), pending.id)).status is (
            ApplicationStatus.PENDING_REVIEW
        )


async def test_finish_requires_processing_pending_and_expires_drafts(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org], status=RaffleStatus.CLOSED)
        pending = await make_application(
            session, raffle=raffle, user=user, organization=org, amount=Decimal("5")
        )
        draft = await make_application(
            session,
            raffle=raffle,
            user=user,
            organization=org,
            amount=Decimal("5"),
            status=ApplicationStatus.DRAFT,
            with_receipt=False,
        )

    async with session_scope(session_factory) as session:
        raffle_row = await session.get(type(raffle), raffle.id)
        with pytest.raises(InvalidStateError):
            await finish(
                session,
                actor=ADMIN,
                raffle=raffle_row,
                expire_pending=False,
                now=datetime.now(UTC),
            )

    async with session_scope(session_factory) as session:
        raffle_row = await session.get(type(raffle), raffle.id)
        result = await finish(
            session,
            actor=ADMIN,
            raffle=raffle_row,
            expire_pending=True,
            now=datetime.now(UTC),
        )
        assert result.expired_count == 2
    async with session_factory() as session:
        assert (await session.get(type(pending), pending.id)).status is (ApplicationStatus.EXPIRED)
        assert (await session.get(type(draft), draft.id)).status is ApplicationStatus.EXPIRED
        raffle_row = await session.get(type(raffle), raffle.id)
        assert raffle_row.status is RaffleStatus.FINISHED


async def test_print_pdf_only_after_finish(session_factory):
    async with session_scope(session_factory) as session:
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
    with pytest.raises(ExportError):
        build_tickets_pdf(raffle)
    async with session_scope(session_factory) as session:
        raffle_row = await session.get(type(raffle), raffle.id)
        raffle_row.status = RaffleStatus.FINISHED
        raffle_row.last_ticket_number = 3
    async with session_factory() as session:
        raffle_row = await session.get(type(raffle), raffle.id)
        export = build_tickets_pdf(raffle_row)
        assert export.data.startswith(b"%PDF-")


async def test_duplicate_raffle_resets_counter_and_dates(session_factory):
    async with session_scope(session_factory) as session:
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org], last_ticket_number=42)
    async with session_scope(session_factory) as session:
        raffle_row = await session.get(type(raffle), raffle.id)
        now = datetime.now(UTC)
        copy = await duplicate(session, actor=ADMIN, raffle=raffle_row, now=now)
        assert copy.id != raffle.id
        assert copy.last_ticket_number == 0
        assert copy.start_at > now
        assert copy.status is RaffleStatus.DRAFT


async def test_input_validation(session_factory):
    with pytest.raises(ValidationError):
        validate_input(
            RaffleInput(
                title="X",
                description="",
                ticket_price=Decimal("0"),
                min_amount=Decimal("0"),
                prizes_count=1,
                start_at=datetime.now(UTC),
                end_at=datetime.now(UTC) + timedelta(days=1),
                channel_url="",
                channel_title="",
            )
        )


async def test_stop_acceptance_then_finish_allowed(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        draft = await make_application(
            session,
            raffle=raffle,
            user=user,
            organization=org,
            amount=Decimal("5"),
            status=ApplicationStatus.AWAITING_RECEIPT,
        )
    async with session_scope(session_factory) as session:
        raffle_row = await session.get(type(raffle), raffle.id)
        await stop_acceptance(session, actor=ADMIN, raffle=raffle_row, now=datetime.now(UTC))
        assert raffle_row.status is RaffleStatus.CLOSED
    async with session_factory() as session:
        assert (await session.get(type(draft), draft.id)).status is ApplicationStatus.EXPIRED


async def test_activation_requires_active_payment_method(session_factory):
    async with session_scope(session_factory) as session:
        org = await make_organization(session, with_method=False)
        raffle = await make_raffle(session, organizations=[org], status=RaffleStatus.DRAFT)
    async with session_scope(session_factory) as session:
        raffle = await session.get(type(raffle), raffle.id)
        with pytest.raises(ValidationError):
            await activate(session, actor=ADMIN, raffle=raffle, now=datetime.now(UTC))
