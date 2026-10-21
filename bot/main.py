"""Bot entry point."""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from sqlalchemy import text

from bot.config import ConfigurationError, Settings, get_settings
from bot.db.session import create_engine, create_session_factory, session_scope
from bot.handlers import admin as admin_handlers
from bot.handlers import common, my, participant, receipts, review
from bot.health import start_health_server
from bot.logging import configure_logging
from bot.middlewares.auth import AuthMiddleware
from bot.middlewares.db import DbSessionMiddleware
from bot.middlewares.throttle import ThrottleMiddleware
from bot.scheduler import BotScheduler
from bot.services import notifications
from bot.services.ratelimit import RateLimiter
from bot.services.roles import sync_admin_roles
from bot.services.storage import ReceiptStorage
from bot.utils.time import utcnow

logger = logging.getLogger(__name__)


async def _startup_checks(env: Settings, storage: ReceiptStorage, engine) -> None:
    from sqlalchemy import text

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("PostgreSQL is not available: %s", exc)
        raise
    try:
        await storage.ensure_bucket()
    except Exception as exc:
        logger.error("RustFS is not available: %s", exc)
        raise


async def _sync_admins(session_factory, env: Settings) -> tuple[list[int], list[int]]:
    async with session_scope(session_factory) as session:
        granted, revoked = await sync_admin_roles(session, env.admin_id_set, now=utcnow())
    if granted or revoked:
        logger.info("Admin sync: granted=%s revoked=%s", granted, revoked)
    return granted, revoked


def bot_commands() -> list[BotCommand]:
    return [
        BotCommand(command="start", description="Участвовать в розыгрыше"),
        BotCommand(command="my", description="Мои заявки"),
        BotCommand(command="help", description="Помощь"),
        BotCommand(command="admin", description="Админ-панель"),
    ]


async def run() -> None:
    try:
        env = get_settings()
    except ConfigurationError as exc:
        logging.basicConfig(level="INFO")
        logger.error("%s", exc)
        raise SystemExit(2) from exc

    configure_logging(env.log_level)
    logger.info("Starting raffle bot in timezone %s", env.tz)

    engine = create_engine(env.database_url)
    session_factory = create_session_factory(engine)
    storage = ReceiptStorage(
        endpoint=env.rustfs_endpoint,
        access_key=env.rustfs_access_key,
        secret_key=env.rustfs_secret_key,
        bucket=env.rustfs_bucket,
        region=env.rustfs_region,
        use_ssl=env.rustfs_use_ssl,
        force_path_style=env.rustfs_force_path_style,
    )
    await _startup_checks(env, storage, engine)
    admin_granted, admin_revoked = await _sync_admins(session_factory, env)

    bot = Bot(
        token=env.bot_token,
        default=DefaultBotProperties(parse_mode=None),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    limiter = RateLimiter()

    dispatcher.update.outer_middleware(DbSessionMiddleware(session_factory))
    dispatcher.update.outer_middleware(AuthMiddleware(env))
    dispatcher.update.outer_middleware(ThrottleMiddleware(limiter))
    dispatcher.workflow_data.update(
        storage=storage,
        limiter=limiter,
        env_settings=env,
    )

    dispatcher.include_router(common.router)
    dispatcher.include_router(admin_handlers.admin_router)
    dispatcher.include_router(my.router)
    dispatcher.include_router(review.router)
    dispatcher.include_router(participant.router)
    dispatcher.include_router(receipts.router)

    for user_id in admin_granted:
        await notifications.send_text(bot, user_id, "Вам выдана роль администратора в боте\\.")
    for user_id in admin_revoked:
        await notifications.send_text(bot, user_id, "Роль администратора в боте отозвана\\.")
    try:
        await bot.set_my_commands(bot_commands())
    except Exception as exc:
        logger.warning("Could not set bot commands: %s", exc)

    async def _readiness() -> bool:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return await storage.healthcheck()
        except Exception:
            return False

    scheduler = BotScheduler(session_factory=session_factory, bot=bot, env=env, storage=storage)
    health_server = await start_health_server(env.health_port, _readiness)
    await scheduler.start()

    try:
        if env.webhook_url:
            await _run_webhook(env, bot, dispatcher, health_server)
        else:
            await bot.delete_webhook(drop_pending_updates=False)
            await dispatcher.start_polling(
                bot, allowed_updates=dispatcher.resolve_used_update_types()
            )
    finally:
        scheduler.shutdown()
        health_server.close()
        with suppress(Exception):
            await health_server.wait_closed()
        with suppress(Exception):
            await bot.session.close()
        await engine.dispose()
        logger.info("Bot stopped")


async def _run_webhook(
    env: Settings,
    bot: Bot,
    dispatcher: Dispatcher,
    health_server: asyncio.AbstractServer,
) -> None:
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
    from aiohttp import web

    app = web.Application()
    handler = SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
        secret_token=env.webhook_secret or None,
    )
    handler.register(app, path=env.webhook_path)
    setup_application(app, dispatcher, bot=bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", env.webhook_port)
    await site.start()
    await bot.set_webhook(
        env.webhook_url,
        secret_token=env.webhook_secret or None,
        drop_pending_updates=False,
    )
    logger.info("Webhook listening on port %s", env.webhook_port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        health_server.close()
        with suppress(Exception):
            await runner.cleanup()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception:
        logging.getLogger(__name__).exception("Fatal error")
        raise


if __name__ == "__main__":
    main()
