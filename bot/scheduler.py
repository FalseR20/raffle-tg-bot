"""Background scheduler: deadlines, draft expiry, retries and retention."""

from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.config import Settings
from bot.db.repositories import raffles as raffles_repo
from bot.db.session import session_scope
from bot.services.cards import resend_missing_cards
from bot.services.lifecycle import (
    cleanup_receipt_objects,
    notify_lifecycle_admins,
    retry_user_notifications,
    run_lifecycle,
)
from bot.services.settings import load_runtime_settings
from bot.services.storage import ReceiptStorage
from bot.utils.time import utcnow

logger = logging.getLogger(__name__)


class BotScheduler:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        bot: Bot,
        env: Settings,
        storage: ReceiptStorage,
    ) -> None:
        self.session_factory = session_factory
        self.bot = bot
        self.env = env
        self.storage = storage
        self.scheduler = AsyncIOScheduler(timezone=env.timezone)
        self._chat_health: dict[int, bool] = {}

    async def tick(self) -> None:
        report = None
        try:
            async with session_scope(self.session_factory) as session:
                report = await run_lifecycle(session, now=utcnow())
        except Exception:
            logger.exception("Lifecycle tick failed")
        if report is not None:
            if report.activated or report.closed or report.blocked:
                await notify_lifecycle_admins(self.bot, self.env.admin_id_set, report)
            if report.expired_drafts:
                logger.info("Expired %s drafts", len(report.expired_drafts))

        try:
            async with session_scope(self.session_factory) as session:
                settings = await load_runtime_settings(session, self.env)
                await retry_user_notifications(session, self.bot, settings=settings)
            async with session_scope(self.session_factory) as session:
                settings = await load_runtime_settings(session, self.env)
                await resend_missing_cards(
                    session,
                    self.bot,
                    settings=settings,
                    storage=self.storage,
                    env_admin_ids=self.env.admin_id_set,
                    now=utcnow(),
                )
        except Exception:
            logger.exception("Notification retry failed")

    async def check_chat_health(self) -> None:
        try:
            async with session_scope(self.session_factory) as session:
                raffle = await raffles_repo.get_current(session)
                organizations = (
                    await raffles_repo.enabled_organizations(session, raffle.id)
                    if raffle is not None
                    else []
                )
        except Exception:
            logger.exception("Chat health query failed")
            return

        failures = []
        states: dict[int, bool] = {}
        for organization in organizations:
            try:
                await self.bot.get_chat(organization.review_chat_id)
                states[organization.id] = True
            except Exception as exc:
                states[organization.id] = False
                if self._chat_health.get(organization.id) is not False:
                    failures.append((organization, exc))
        if failures:
            lines = [
                "⚠️ Бот не может писать в чат проверки:",
                *[f"• {organization.name}: {str(exc)[:120]}" for organization, exc in failures],
            ]
            for admin_id in self.env.admin_id_set:
                try:
                    await self.bot.send_message(admin_id, "\n".join(lines))
                except Exception:
                    logger.warning("Could not alert admin %s about chat health", admin_id)
        self._chat_health = states

    async def cleanup(self) -> None:
        try:
            async with session_scope(self.session_factory) as session:
                settings = await load_runtime_settings(session, self.env)
                removed = await cleanup_receipt_objects(
                    session,
                    self.storage,
                    retention_days=settings.receipt_retention_days,
                    now=utcnow(),
                )
                if removed:
                    logger.info("Deleted %s receipt objects after retention", removed)
        except Exception:
            logger.exception("Receipt retention cleanup failed")

    async def start(self) -> None:
        self.scheduler.add_job(
            self.tick,
            IntervalTrigger(seconds=60),
            id="lifecycle",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self.check_chat_health,
            IntervalTrigger(minutes=15),
            id="chat-health",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self.cleanup,
            CronTrigger(hour=3, minute=30),
            id="receipt-retention",
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()
        await self.tick()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
