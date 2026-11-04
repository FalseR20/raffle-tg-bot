"""Telegram message delivery with MarkdownV2 fallback and retries."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardMarkup, Message

from bot.logging import get_logger
from bot.texts.markdown import (
    MESSAGE_LIMIT,
    is_valid_markdown_v2,
    to_plain_text,
    truncate,
)

logger = get_logger(__name__)

RETRY_DELAYS = (0.5, 1.5, 3.0)

_edit_target: ContextVar[tuple[int, int] | None] = ContextVar("edit_target", default=None)


@contextmanager
def editing_target(chat_id: int, message_id: int) -> Iterator[None]:
    """Edit this message with ``send_text`` calls made inside the block."""
    token = _edit_target.set((chat_id, message_id))
    try:
        yield
    finally:
        _edit_target.reset(token)


@dataclass(frozen=True)
class DeliveryResult:
    message: Message | None
    used_plain_text: bool
    error: str | None = None
    unchanged: bool = False

    @property
    def ok(self) -> bool:
        return self.message is not None or self.unchanged


async def send_text(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    reply_markup=None,
    message_thread_id: int | None = None,
    fallback: bool = True,
    new_message: bool = False,
) -> DeliveryResult:
    """Send MarkdownV2; on a parse error resend the same text without markup.

    When called inside :func:`editing_target` and the target chat matches, the
    existing message is edited instead of sending a new one. Pass
    ``new_message=True`` for intentional follow-up messages.
    """
    target = None if new_message else _edit_target.get()
    if target is not None and target[0] == chat_id:
        return await _edit_text(
            bot,
            chat_id=chat_id,
            message_id=target[1],
            text=text,
            reply_markup=reply_markup,
            fallback=fallback,
        )

    kwargs = {}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    if message_thread_id is not None:
        kwargs["message_thread_id"] = message_thread_id

    if not is_valid_markdown_v2(text):
        logger.debug("MarkdownV2 validation failed; sending plain text")
        plain = truncate(to_plain_text(text), MESSAGE_LIMIT)
        try:
            message = await _send_plain(bot, chat_id, plain, **kwargs)
            return DeliveryResult(message=message, used_plain_text=True)
        except Exception as exc:
            return DeliveryResult(message=None, used_plain_text=True, error=str(exc))

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
            if "reply_markup" in kwargs:
                logger.warning("Message rejected with reply markup; retrying without it: %s", exc)
                without_markup = {
                    key: value for key, value in kwargs.items() if key != "reply_markup"
                }
                try:
                    message = await _send_with_retry(
                        bot,
                        chat_id,
                        truncate(text, MESSAGE_LIMIT),
                        parse_mode="MarkdownV2",
                        **without_markup,
                    )
                    return DeliveryResult(message=message, used_plain_text=False)
                except Exception as retry_exc:
                    return DeliveryResult(message=None, used_plain_text=False, error=str(retry_exc))
            return DeliveryResult(message=None, used_plain_text=False, error=str(exc))
        logger.warning("MarkdownV2 rejected by Telegram; falling back to plain text: %s", exc)
        plain = truncate(to_plain_text(text), MESSAGE_LIMIT)
        try:
            message = await _send_plain(bot, chat_id, plain, **kwargs)
            return DeliveryResult(message=message, used_plain_text=True)
        except Exception as plain_exc:
            return DeliveryResult(message=None, used_plain_text=True, error=str(plain_exc))
    except Exception as exc:
        return DeliveryResult(message=None, used_plain_text=False, error=str(exc))


async def _edit_text(
    bot: Bot,
    *,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup,
    fallback: bool,
) -> DeliveryResult:
    kwargs = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": reply_markup
        if reply_markup is not None
        else InlineKeyboardMarkup(inline_keyboard=[]),
    }

    if not is_valid_markdown_v2(text):
        logger.debug("MarkdownV2 validation failed; editing as plain text")
        plain = truncate(to_plain_text(text), MESSAGE_LIMIT)
        try:
            edited = await _edit_with_retry(bot, plain, parse_mode=None, **kwargs)
            return _edited_result(edited, used_plain_text=True)
        except TelegramBadRequest as exc:
            if _is_not_modified(exc):
                return DeliveryResult(message=None, used_plain_text=True, unchanged=True)
            return await _send_after_failed_edit(
                bot,
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                fallback=fallback,
            )
        except Exception as exc:
            logger.warning("Message edit failed; sending a new message: %s", exc)
            return await _send_after_failed_edit(
                bot,
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                fallback=fallback,
            )

    try:
        edited = await _edit_with_retry(
            bot,
            truncate(text, MESSAGE_LIMIT),
            parse_mode="MarkdownV2",
            **kwargs,
        )
        return _edited_result(edited, used_plain_text=False)
    except TelegramBadRequest as exc:
        if _is_not_modified(exc):
            return DeliveryResult(message=None, used_plain_text=False, unchanged=True)
        if fallback and "parse" in str(exc).lower():
            logger.warning("MarkdownV2 rejected by Telegram on edit; retrying as plain: %s", exc)
            plain = truncate(to_plain_text(text), MESSAGE_LIMIT)
            try:
                edited = await _edit_with_retry(bot, plain, parse_mode=None, **kwargs)
                return _edited_result(edited, used_plain_text=True)
            except TelegramBadRequest as plain_exc:
                if _is_not_modified(plain_exc):
                    return DeliveryResult(message=None, used_plain_text=True, unchanged=True)
            except Exception as plain_exc:
                return DeliveryResult(message=None, used_plain_text=True, error=str(plain_exc))
        return await _send_after_failed_edit(
            bot,
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            fallback=fallback,
        )
    except Exception as exc:
        logger.warning("Message edit failed; sending a new message: %s", exc)
        return await _send_after_failed_edit(
            bot,
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            fallback=fallback,
        )


async def _send_after_failed_edit(
    bot: Bot, *, chat_id: int, text: str, reply_markup, fallback: bool
) -> DeliveryResult:
    logger.debug("Message could not be edited; sending a new one")
    return await send_text(
        bot,
        chat_id,
        text,
        reply_markup=reply_markup,
        fallback=fallback,
        new_message=True,
    )


def _edited_result(edited: Message | bool, *, used_plain_text: bool) -> DeliveryResult:
    if isinstance(edited, Message):
        return DeliveryResult(message=edited, used_plain_text=used_plain_text)
    return DeliveryResult(message=None, used_plain_text=used_plain_text, unchanged=True)


def _is_not_modified(exc: TelegramBadRequest) -> bool:
    return "not modified" in str(exc).lower()


async def _edit_with_retry(bot: Bot, text: str, **kwargs):
    last_error: Exception | None = None
    for attempt, delay in enumerate((0.0, *RETRY_DELAYS)):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await bot.edit_message_text(text=text, **kwargs)
        except TelegramRetryAfter as exc:
            last_error = exc
            await asyncio.sleep(exc.retry_after)
        except (TelegramNetworkError, TimeoutError) as exc:
            last_error = exc
            logger.warning("Telegram network error on edit attempt %s: %s", attempt + 1, exc)
        except (TelegramBadRequest, TelegramForbiddenError):
            raise
    raise last_error or RuntimeError("message edit failed")


async def _send_plain(bot: Bot, chat_id: int, text: str, **kwargs) -> Message:
    """Send plain text, dropping an invalid keyboard if necessary."""
    try:
        return await _send_with_retry(bot, chat_id, text, parse_mode=None, **kwargs)
    except TelegramBadRequest:
        if "reply_markup" not in kwargs:
            raise
        logger.warning("Plain message rejected with reply markup; retrying without it")
        without_markup = {key: value for key, value in kwargs.items() if key != "reply_markup"}
        return await _send_with_retry(bot, chat_id, text, parse_mode=None, **without_markup)


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
