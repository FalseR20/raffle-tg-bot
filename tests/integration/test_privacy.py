"""Privacy consent, nickname storage and withdrawal."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bot.db.enums import ApplicationStatus
from bot.db.session import session_scope
from bot.services import users as users_service
from bot.services.errors import ValidationError
from bot.services.privacy import withdraw_consent
from bot.services.settings import environment_defaults
from tests.helpers import make_application, make_organization, make_raffle, make_user

pytestmark = pytest.mark.integration


class RecordingStorage:
    def __init__(self, *, failing: str | None = None) -> None:
        self.deleted: list[str] = []
        self.failing = failing

    async def delete(self, key: str) -> None:
        if key == self.failing:
            raise RuntimeError("storage down")
        self.deleted.append(key)


async def test_consent_requires_operator_identity(session_factory, env_settings):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500)
        settings = replace(
            environment_defaults(env_settings),
            operator_name=None,
            operator_address=None,
        )
        with pytest.raises(ValidationError):
            await users_service.accept_consent(
                session, user, settings=settings, now=datetime.now(UTC), publish_name=None
            )


async def test_withdrawal_removes_nickname_and_receipts(session_factory, env_settings):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500, username="payer")
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org], last_ticket_number=1)
        pending = await make_application(
            session, raffle=raffle, user=user, organization=org, amount=Decimal("5")
        )
        pending.receipt_object_key = "receipts/pending.pdf"
        confirmed = await make_application(
            session,
            raffle=raffle,
            user=user,
            organization=org,
            amount=Decimal("10"),
        )
        confirmed.status = ApplicationStatus.CONFIRMED
        confirmed.tickets_awarded = 1
        confirmed.first_ticket_number = 1
        confirmed.last_ticket_number = 1
        confirmed.tickets_total_after = 1
        confirmed.receipt_object_key = "receipts/confirmed.pdf"
        confirmed.card_chat_id = -100
        confirmed.card_message_id = 7
        settings = environment_defaults(env_settings)
        await users_service.accept_consent(
            session,
            user,
            settings=settings,
            now=datetime.now(UTC),
            publish_name=True,
            profile=users_service.TelegramProfile(500, "payer", "Иван", "Петров", "ru"),
        )
        assert user.username == "payer"
        storage = RecordingStorage()
        result = await withdraw_consent(
            session,
            user=user,
            storage=storage,
            now=datetime.now(UTC),
            keep_profile=False,
        )
        assert user.username is None
        assert user.first_name == ""
        assert user.privacy_accepted_at is None
        assert user.publish_name_consent_at is None
        assert not users_service.has_valid_consent(user, settings)
        assert {item.public_id for item in result.cancelled} == {pending.public_id}
        assert result.review_messages == ((-100, 7),)
        assert result.receipts_failed == 0
        assert storage.deleted == ["receipts/pending.pdf", "receipts/confirmed.pdf"]
        await session.refresh(confirmed)
        assert confirmed.receipt_object_key is None
        assert confirmed.receipt_file_id is None
        assert confirmed.first_ticket_number == 1
        assert confirmed.status is ApplicationStatus.CONFIRMED


async def test_failed_receipt_delete_keeps_the_application(session_factory, env_settings):
    async with session_scope(session_factory) as session:
        user = await make_user(session, 500, username="payer")
        org = await make_organization(session)
        raffle = await make_raffle(session, organizations=[org])
        pending = await make_application(
            session, raffle=raffle, user=user, organization=org, amount=Decimal("5")
        )
        pending.receipt_object_key = "receipts/stuck.pdf"
        await session.flush()
        result = await withdraw_consent(
            session,
            user=user,
            storage=RecordingStorage(failing="receipts/stuck.pdf"),
            now=datetime.now(UTC),
            keep_profile=False,
        )
        assert result.receipts_failed == 1
        assert result.cancelled == ()
        await session.refresh(pending)
        assert pending.receipt_object_key == "receipts/stuck.pdf"
        assert user.username is None
