"""Withdraw privacy consent and delete the data that consent covered.

Ticket numbers already issued stay linked to an internal id without the
nickname or receipt. Unfinished applications are cancelled. Receipt files are
deleted immediately; a failed delete can be retried by withdrawing again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Application, User
from bot.db.repositories import applications as apps_repo
from bot.db.repositories import audit as audit_repo
from bot.logging import get_logger
from bot.services.applications import CancelledApplication, cancel_by_user
from bot.services.users import forget_profile

logger = get_logger(__name__)


class DeletesObjects(Protocol):
    async def delete(self, key: str) -> None: ...


@dataclass(frozen=True)
class PrivacyWithdrawal:
    cancelled: tuple[CancelledApplication, ...]
    review_messages: tuple[tuple[int, int], ...]
    receipts_failed: int
    profile_cleared: bool


def _clear_receipt_fields(application: Application) -> None:
    application.receipt_type = None
    application.receipt_file_id = None
    application.receipt_file_unique_id = None
    application.receipt_object_key = None
    application.receipt_etag = None
    application.receipt_mime = None
    application.receipt_size = None


async def _delete_object(storage: DeletesObjects, key: str) -> bool:
    try:
        await storage.delete(key)
    except Exception as exc:
        logger.warning("Could not delete receipt %s after privacy withdrawal: %s", key, exc)
        return False
    return True


async def withdraw_consent(
    session: AsyncSession,
    *,
    user: User,
    storage: DeletesObjects,
    now: datetime,
    keep_profile: bool,
) -> PrivacyWithdrawal:
    """Clear consent, drop the nickname, and erase receipt files."""
    cancelled: list[CancelledApplication] = []
    review_messages: list[tuple[int, int]] = []
    failed = 0
    application_ids = [row.id for row in await apps_repo.all_for_user(session, user.id)]

    for application_id in application_ids:
        locked = await apps_repo.get_locked(session, application_id)
        if locked is None:
            continue
        key = locked.receipt_object_key
        if key and not await _delete_object(storage, key):
            failed += 1
            await audit_repo.log(
                session,
                entity_type="application",
                entity_id=locked.id,
                application_id=locked.id,
                raffle_id=locked.raffle_id,
                actor_id=user.id,
                action="receipt_delete_failed",
                payload={"reason": "privacy_withdrawal"},
                at=now,
            )
            continue
        if locked.status.is_cancellable_by_user:
            cancelled.append(
                await cancel_by_user(session, application=locked, user_id=user.id, now=now)
            )
            continue
        card = (locked.card_chat_id, locked.card_message_id)
        _clear_receipt_fields(locked)
        if card[0] is not None and card[1] is not None:
            review_messages.append((card[0], card[1]))
        if key:
            await audit_repo.log(
                session,
                entity_type="application",
                entity_id=locked.id,
                application_id=locked.id,
                raffle_id=locked.raffle_id,
                actor_id=user.id,
                action="receipt_deleted",
                payload={"reason": "privacy_withdrawal"},
                at=now,
            )

    user.privacy_accepted_at = None
    user.privacy_version = None
    user.publish_name_consent_at = None
    if not keep_profile:
        await forget_profile(session, user)
    await audit_repo.log(
        session,
        entity_type="user",
        entity_id=user.id,
        actor_id=user.id,
        action="privacy_withdrawn",
        payload={
            "cancelled": len(cancelled),
            "receipts_failed": failed,
            "profile_cleared": not keep_profile,
        },
        at=now,
    )
    await session.flush()
    return PrivacyWithdrawal(
        cancelled=tuple(cancelled),
        review_messages=tuple(review_messages),
        receipts_failed=failed,
        profile_cleared=not keep_profile,
    )
