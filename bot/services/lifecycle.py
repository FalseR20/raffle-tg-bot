"""Periodic lifecycle, notification retries and receipt retention."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db.enums import ApplicationStatus, RaffleStatus
from bot.db.models import Application, Raffle
from bot.db.repositories import audit as audit_repo
from bot.services import applications as applications_service
from bot.services import notifications
from bot.services.raffles import process_schedule
from bot.services.settings import RuntimeSettings
from bot.services.storage import ReceiptStorage
from bot.texts import ru
from bot.texts.markdown import escape_markdown_v2
from bot.utils.time import utcnow


@dataclass
class LifecycleReport:
    activated: list = field(default_factory=list)
    closed: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    expired_drafts: list = field(default_factory=list)


async def run_lifecycle(session: AsyncSession, *, now: datetime) -> LifecycleReport:
    activated, closed, blocked = await process_schedule(session, now=now)
    expired = await applications_service.expire_drafts(session, now=now)
    return LifecycleReport(
        activated=activated, closed=closed, blocked=blocked, expired_drafts=expired
    )


async def retry_user_notifications(
    session: AsyncSession,
    bot: Bot,
    *,
    settings: RuntimeSettings,
    limit: int = 20,
) -> int:
    stmt = (
        select(Application)
        .where(
            Application.status == ApplicationStatus.CONFIRMED,
            Application.user_notified_at.is_(None),
            Application.notification_error.is_not(None),
        )
        .order_by(Application.reviewed_at)
        .limit(limit)
        .options(
            selectinload(Application.user),
            selectinload(Application.organization),
            selectinload(Application.raffle),
        )
    )
    applications = list((await session.execute(stmt)).scalars())
    delivered = 0
    for application in applications:
        total, tickets, ranges = await applications_service.user_totals(
            session, application.raffle_id, application.user_id
        )
        text = ru.user_confirmation_text(
            public_id=application.public_id,
            amount=application.amount or 0,
            organization_name=application.organization.name,
            total_amount=total,
            new_numbers=(
                f"{application.first_ticket_number}–{application.last_ticket_number}"
                if application.tickets_awarded > 1
                else str(application.first_ticket_number)
            ),
            total_tickets=tickets,
            all_numbers=ru.ranges_display(ranges),
            channel_title=application.raffle.channel_title or settings.channel_title,
            channel_url=application.raffle.channel_url or settings.channel_url,
        )
        result = await notifications.send_text(bot, application.user_id, text)
        if result.ok:
            application.user_notified_at = utcnow()
            application.notification_error = None
            delivered += 1
            await audit_repo.log(
                session,
                entity_type="application",
                entity_id=application.id,
                application_id=application.id,
                raffle_id=application.raffle_id,
                actor_id=None,
                action="user_notified_retry",
                at=utcnow(),
            )
        else:
            application.notification_error = (result.error or "unknown")[:500]
    if applications:
        await session.flush()
    return delivered


async def cleanup_receipt_objects(
    session: AsyncSession,
    storage: ReceiptStorage,
    *,
    retention_days: int,
    now: datetime,
) -> int:
    """Delete RustFS objects after retention; metadata and audit remain."""
    cutoff = now - timedelta(days=retention_days)
    stmt = (
        select(Application)
        .join(Raffle, Raffle.id == Application.raffle_id)
        .where(
            Application.receipt_object_key.is_not(None),
            Raffle.status.in_([RaffleStatus.FINISHED, RaffleStatus.ARCHIVED]),
            Raffle.finished_at.is_not(None),
            Raffle.finished_at < cutoff,
        )
    )
    removed = 0
    for application in (await session.execute(stmt)).scalars():
        key = application.receipt_object_key
        if not key:
            continue
        try:
            await storage.delete(key)
        except Exception as exc:
            await audit_repo.log(
                session,
                entity_type="application",
                entity_id=application.id,
                application_id=application.id,
                raffle_id=application.raffle_id,
                actor_id=None,
                action="receipt_delete_failed",
                payload={"error": str(exc)[:300]},
                at=now,
            )
            continue
        application.receipt_object_key = None
        removed += 1
        await audit_repo.log(
            session,
            entity_type="application",
            entity_id=application.id,
            application_id=application.id,
            raffle_id=application.raffle_id,
            actor_id=None,
            action="receipt_deleted",
            payload={"object_key": key, "reason": "retention"},
            at=now,
        )
    if removed:
        await session.flush()
    return removed


async def notify_lifecycle_admins(
    bot: Bot, admin_ids: frozenset[int], report: LifecycleReport
) -> None:
    for raffle in report.activated:
        await notifications.notify_admins(
            bot,
            admin_ids,
            f"▶️ Розыгрыш «{escape_markdown_v2(raffle.title)}» начался по расписанию\\.",
        )
    for raffle in report.closed:
        await notifications.notify_admins(
            bot,
            admin_ids,
            f"⏹ Розыгрыш «{escape_markdown_v2(raffle.title)}» закрыт по расписанию\\.",
        )
    for raffle, reason in report.blocked:
        await notifications.notify_admins(
            bot,
            admin_ids,
            f"⚠️ Запланированный розыгрыш «{escape_markdown_v2(raffle.title)}» "
            f"не запущен: {escape_markdown_v2(reason)}",
        )


__all__ = [
    "LifecycleReport",
    "cleanup_receipt_objects",
    "notify_lifecycle_admins",
    "retry_user_notifications",
    "run_lifecycle",
]
