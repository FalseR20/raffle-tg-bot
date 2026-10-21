"""Rate limit middleware for /start and other command floods."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from bot.services.ratelimit import RateLimiter


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, limiter: RateLimiter) -> None:
        self.limiter = limiter

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if (
            isinstance(event, Message)
            and event.text
            and event.text.startswith("/start")
            and event.from_user
            and not self.limiter.allow("start", event.from_user.id, limit=1, window_seconds=1.0)
        ):
            return None
        return await handler(event, data)
