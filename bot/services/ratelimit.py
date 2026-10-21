"""Simple in-memory rate limiting.

The bot runs as a single instance (see deployment notes); limits reset on
restart. Database constraints still enforce business limits such as the number
of applications per user per raffle.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, int], deque[float]] = defaultdict(deque)

    def allow(self, action: str, user_id: int, *, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        key = (action, user_id)
        hits = self._hits[key]
        while hits and now - hits[0] > window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True

    def reset(self, action: str | None = None, user_id: int | None = None) -> None:
        if action is None:
            self._hits.clear()
            return
        for key in list(self._hits):
            if key[0] == action and (user_id is None or key[1] == user_id):
                self._hits.pop(key, None)
