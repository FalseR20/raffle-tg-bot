"""Telegram message delivery with MarkdownV2 fallback and retries."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import Message

from bot.logging import get_logger
from bot.texts.markdown import MESSAGE_LIMIT, to_plain_text, truncate

logger = get_logger(__name__)

RETRY_DELAYS = (0.5, 1.5, 3.0)


@dataclass(frozen=True)
class DeliveryResult:
    message: Message | None
    used_plain_text: bool
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.message is not None


async def send_text(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    reply_markup=None,
    message_thread_id: int | None = None,
    fallback: bool = True,
) -> DeliveryResult:
    """Send MarkdownV2; on a parse error resend the same text without markup."""
    kwargs = {}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    if message_thread_id is not None:
        kwargs["message_thread_id"] = message_thread_id
    try:
        message = await _send_with_retry(
            bot,
            chat_id,
            truncate(text, MESSAGE_LIMIT),
            parse_mode="MarkdownV2",
            **kwargs,
        )
        return DeliveryResult(message=message, used_plain_text=False)
    except TelegramBadRequest as exc:
        if not fallback or "parse" not in str(exc).lower():
            return DeliveryResult(message=None, used_plain_text=False, error=str(exc))
        logger.warning("MarkdownV2 rejected by Telegram; falling back to plain text: %s", exc)
        plain = truncate(to_plain_text(text), MESSAGE_LIMIT)
        try:
            message = await _send_with_retry(bot, chat_id, plain, parse_mode=None, **kwargs)
            return DeliveryResult(message=message, used_plain_text=True)
        except Exception as plain_exc:
            return DeliveryResult(message=None, used_plain_text=True, error=str(plain_exc))
    except Exception as exc:
        return DeliveryResult(message=None, used_plain_text=False, error=str(exc))


async def _send_with_retry(bot: Bot, chat_id: int, text: str, **kwargs) -> Message:
    last_error: Exception | None = None
    for attempt, delay in enumerate((0.0, *RETRY_DELAYS)):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await bot.send_message(chat_id, text, **kwargs)
        except TelegramRetryAfter as exc:
            last_error = exc
            await asyncio.sleep(exc.retry_after)
        except (TelegramNetworkError, TimeoutError) as exc:
            last_error = exc
            logger.warning("Telegram network error on attempt %s: %s", attempt + 1, exc)
        except (TelegramBadRequest, TelegramForbiddenError):
            raise
    raise last_error or RuntimeError("message delivery failed")


async def notify_admins(bot: Bot, admin_ids: frozenset[int], text: str) -> None:
    for admin_id in admin_ids:
        result = await send_text(bot, admin_id, text)
        if not result.ok:
            logger.error("Could not deliver admin notification to %s: %s", admin_id, result.error)
