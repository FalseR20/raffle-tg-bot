"""Lifecycle and raffle rules integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from bot.db.enums import ApplicationStatus, RaffleStatus
from bot.db.models import (
    Application,
    AuditLog,
    Organization,
    ParticipantTotal,
    Raffle,
    User,
    Winner,
)
from bot.db.repositories import audit as audit_repo
from bot.db.repositories import raffles as raffles_repo
from bot.db.repositories import winners as winners_repo
from bot.db.session import session_scope
from bot.services.errors import (
    ExportError,
    InvalidStateError,
    PermissionDeniedError,
    StorageError,
    ValidationError,
)
from bot.services.exports import build_tickets_pdf
from bot.services.lifecycle import purge_cancelled_applications
from bot.services.permissions import Permissions
from bot.services.raffles import (
    RaffleInput,
    activate,
    delete_archived,
    duplicate,
    finish,
    process_schedule,
    purge_archived_receipts,
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
ORGANIZER = Permissions(user_id=200, is_organizer=True)
ORG_REP = Permissions(user_id=300, org_rep_ids=frozenset({1}))


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


class _FlakyStorage:
    def __init__(self, failing_key: str) -> None:
        self.failing_key = failing_key
        self.deleted: list[str] = []

    async def delete(self, key: str) -> None:
        if key == self.failing_key:
            raise StorageError("Could not delete receipt: boom")
        self.deleted.append(key)


async def test_archived_raffle_deletion_removes_dependents_and_keeps_audit(session_factory):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        org = await make_organization(session)
        archived = await make_raffle(
            session,
            organizations=[org],
            title="Archive me",
            status=RaffleStatus.ARCHIVED,
            last_ticket_number=2,
        )
        archived.finished_at = datetime.now(UTC)
        kept = await make_raffle(
            session, organizations=[org], title="Keep me", status=RaffleStatus.ARCHIVED
        )
        confirmed = await make_application(
            session, raffle=archived, user=user, organization=org, amount=Decimal("10")
        )
        confirmed.status = ApplicationStatus.CONFIRMED
        confirmed.tickets_awarded = 2
        confirmed.first_ticket_number = 1
        confirmed.last_ticket_number = 2
        confirmed.tickets_total_after = 2
        confirmed.receipt_object_key = "receipts/archive/confirmed.pdf"
        rejected = await make_application(
            session,
            raffle=archived,
            user=user,
            organization=org,
            amount=Decimal("5"),
            status=ApplicationStatus.REJECTED,
        )
        rejected.receipt_object_key = "receipts/archive/rejected.pdf"
        await winners_repo.create(
            session,
            raffle_id=archived.id,
            place=1,
            ticket_number=1,
            application_id=confirmed.id,
        )
        kept_app = await make_application(
            session,
            raffle=kept,
            user=user,
            organization=org,
            amount=Decimal("7"),
            status=ApplicationStatus.REJECTED,
        )
        await audit_repo.log(
            session,
            entity_type="application",
            entity_id=confirmed.id,
            application_id=confirmed.id,
            raffle_id=archived.id,
            actor_id=ADMIN.user_id,
            action="application_confirmed",
        )
        archived_id = archived.id
        kept_id = kept.id
        kept_app_id = kept_app.id
        org_id = org.id
        user_id = user.id
        confirmed_id = confirmed.id
        rejected_id = rejected.id

    async with session_scope(session_factory) as session:
        raffle = await raffles_repo.get(session, archived_id)
        assert raffle is not None
        deletion = await delete_archived(
            session, actor=ORGANIZER, raffle=raffle, now=datetime.now(UTC)
        )
        assert deletion.raffle_id == archived_id
        assert set(deletion.receipt_object_keys) == {
            "receipts/archive/confirmed.pdf",
            "receipts/archive/rejected.pdf",
        }

    storage = RecordingStorage()
    async with session_scope(session_factory) as session:
        failed = await purge_archived_receipts(
            session,
            storage,
            keys=deletion.receipt_object_keys,
            raffle_id=archived_id,
            actor_id=ORGANIZER.user_id,
            now=datetime.now(UTC),
        )
        assert failed == ()
    assert set(storage.deleted) == set(deletion.receipt_object_keys)

    async with session_factory() as session:
        assert await session.get(Raffle, archived_id) is None
        assert await session.get(Application, confirmed_id) is None
        assert await session.get(Application, rejected_id) is None
        assert await session.get(ParticipantTotal, (archived_id, user_id)) is None
        winner_count = await session.scalar(
            select(func.count()).select_from(Winner).where(Winner.raffle_id == archived_id)
        )
        assert winner_count == 0
        assert await session.get(Raffle, kept_id) is not None
        assert (await session.get(Application, kept_app_id)).raffle_id == kept_id
        assert await session.get(Organization, org_id) is not None
        assert await session.get(User, user_id) is not None

        deleted_audit = (
            await session.execute(select(AuditLog).where(AuditLog.action == "raffle_deleted"))
        ).scalar_one()
        assert deleted_audit.raffle_id is None
        assert deleted_audit.old_status == RaffleStatus.ARCHIVED.value
        assert deleted_audit.actor_id == ORGANIZER.user_id
        assert deleted_audit.payload["raffle_id"] == archived_id
        assert deleted_audit.payload["title"] == "Archive me"
        assert deleted_audit.payload["applications"] == 2
        assert deleted_audit.payload["winners"] == 1
        assert deleted_audit.payload["last_ticket_number"] == 2

        prior = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "application_confirmed")
            )
        ).scalar_one()
        assert prior.raffle_id is None
        assert prior.application_id is None
        assert prior.entity_id == str(confirmed_id)

    async with session_scope(session_factory) as session:
        org = await session.get(Organization, org_id)
        assert org is not None
        again = await make_raffle(
            session, organizations=[org], title="Archive me", status=RaffleStatus.ARCHIVED
        )
        assert again.id != archived_id


async def test_only_archived_raffle_can_be_deleted_by_a_manager(session_factory):
    async with session_scope(session_factory) as session:
        org = await make_organization(session)
        finished = await make_raffle(
            session, organizations=[org], title="Finished", status=RaffleStatus.FINISHED
        )
        archived = await make_raffle(
            session, organizations=[org], title="Empty archive", status=RaffleStatus.ARCHIVED
        )
        finished_id = finished.id
        archived_id = archived.id

    async with session_scope(session_factory) as session:
        finished = await session.get(Raffle, finished_id)
        with pytest.raises(InvalidStateError):
            await delete_archived(session, actor=ADMIN, raffle=finished, now=datetime.now(UTC))
        archived = await session.get(Raffle, archived_id)
        with pytest.raises(PermissionDeniedError):
            await delete_archived(session, actor=ORG_REP, raffle=archived, now=datetime.now(UTC))

    async with session_factory() as session:
        assert await session.get(Raffle, finished_id) is not None
        assert await session.get(Raffle, archived_id) is not None

    async with session_scope(session_factory) as session:
        archived = await session.get(Raffle, archived_id)
        await delete_archived(session, actor=ADMIN, raffle=archived, now=datetime.now(UTC))

    async with session_factory() as session:
        assert await session.get(Raffle, archived_id) is None
        assert await session.get(Raffle, finished_id) is not None


async def test_failed_receipt_purge_is_audited_without_restoring_raffle(session_factory):
    storage = _FlakyStorage("receipts/archive/missing.pdf")
    async with session_scope(session_factory) as session:
        failed = await purge_archived_receipts(
            session,
            storage,
            keys=("receipts/archive/ok.pdf", "receipts/archive/missing.pdf"),
            raffle_id=41,
            actor_id=ADMIN.user_id,
            now=datetime.now(UTC),
        )
        assert failed == ("receipts/archive/missing.pdf",)
    assert storage.deleted == ["receipts/archive/ok.pdf"]

    async with session_factory() as session:
        entry = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "receipt_delete_failed")
            )
        ).scalar_one()
        assert entry.raffle_id is None
        assert entry.payload["raffle_id"] == 41
        assert entry.payload["object_keys"] == ["receipts/archive/missing.pdf"]
        assert entry.payload["reason"] == "raffle_deleted"


async def test_activation_requires_active_payment_method(session_factory):
    async with session_scope(session_factory) as session:
        org = await make_organization(session, with_method=False)
        raffle = await make_raffle(session, organizations=[org], status=RaffleStatus.DRAFT)
    async with session_scope(session_factory) as session:
        raffle = await session.get(type(raffle), raffle.id)
        with pytest.raises(ValidationError):
            await activate(session, actor=ADMIN, raffle=raffle, now=datetime.now(UTC))
