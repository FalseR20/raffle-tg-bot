"""Ticket arithmetic: cumulative totals, deltas, ranges and display."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from bot.domain.money import decimal_or_zero


@dataclass(frozen=True)
class TicketPreview:
    """Preview of a new application for a participant."""

    entered_amount: Decimal
    current_amount: Decimal
    current_tickets: int
    preview_amount: Decimal
    preview_tickets: int

    @property
    def delta(self) -> int:
        return self.preview_tickets - self.current_tickets


def calculate_tickets(confirmed_total: Decimal, ticket_price: Decimal) -> int:
    """``floor(confirmed_total / ticket_price)`` without float arithmetic."""
    if ticket_price <= 0:
        raise ValueError("ticket_price must be positive")
    if confirmed_total <= 0:
        return 0
    return int(confirmed_total // ticket_price)


def build_preview(
    entered_amount: Decimal,
    *,
    current_amount: Decimal | None,
    current_tickets: int,
    ticket_price: Decimal,
) -> TicketPreview:
    current = decimal_or_zero(current_amount)
    total = current + entered_amount
    return TicketPreview(
        entered_amount=entered_amount,
        current_amount=current,
        current_tickets=current_tickets,
        preview_amount=total,
        preview_tickets=calculate_tickets(total, ticket_price),
    )


def suggest_amount(
    preview: TicketPreview, ticket_price: Decimal, min_amount: Decimal
) -> Decimal | None:
    """Suggest the amount that would use the current remainder fully.

    Returns ``None`` when the preview total is already an exact multiple of the
    ticket price. The suggestion is never below the per-payment minimum and is
    only informational; the minimum itself is enforced independently.
    """
    remainder = preview.preview_amount % ticket_price
    if remainder == 0:
        return None
    needed = ticket_price - remainder
    suggested = max(preview.entered_amount + needed, min_amount)
    return suggested if suggested != preview.entered_amount else None


@dataclass(frozen=True)
class TicketRange:
    first: int
    last: int

    def __post_init__(self) -> None:
        if self.first < 1 or self.last < self.first:
            raise ValueError("invalid ticket range")

    @property
    def count(self) -> int:
        return self.last - self.first + 1

    def __str__(self) -> str:
        if self.first == self.last:
            return str(self.first)
        return f"{self.first}–{self.last}"


def assign_range(last_ticket_number: int, delta: int) -> TicketRange:
    """Return the next contiguous range after ``last_ticket_number``."""
    if delta < 1:
        raise ValueError("delta must be at least 1")
    if last_ticket_number < 0:
        raise ValueError("last_ticket_number must be non-negative")
    return TicketRange(first=last_ticket_number + 1, last=last_ticket_number + delta)


def compress_numbers(numbers: Iterable[int]) -> str:
    """Compress ticket numbers into ranges: ``1, 3–4``."""
    ordered = sorted(set(numbers))
    if not ordered:
        return ""
    parts: list[str] = []
    start = prev = ordered[0]
    for number in ordered[1:]:
        if number == prev + 1:
            prev = number
            continue
        parts.append(str(TicketRange(start, prev)))
        start = prev = number
    parts.append(str(TicketRange(start, prev)))
    return ", ".join(parts)


def compress_ranges(ranges: Sequence[tuple[int, int]]) -> str:
    if not ranges:
        return ""
    merged: list[tuple[int, int]] = []
    for first, last in sorted(ranges):
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    parts = []
    for first, last in merged:
        if first == last:
            parts.append(str(first))
        else:
            parts.append(f"{first}–{last}")
    return ", ".join(parts)


def format_ticket_numbers(ranges: Sequence[tuple[int, int]]) -> str:
    """Format already-assigned ranges, compressing adjacent ones."""
    numbers: list[int] = []
    for first, last in ranges:
        numbers.extend(range(first, last + 1))
    return compress_numbers(numbers)
