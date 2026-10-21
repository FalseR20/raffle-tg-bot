"""Prize cap arithmetic for the optional random draw."""

from __future__ import annotations

from collections.abc import Sequence


def max_prizes_per_ticket(prizes_count: int, tickets_count: int) -> int:
    """``ceil(prizes_count / tickets_count)``, minimum 1.

    10 prizes / 3 tickets -> 4; 10 / 100 -> 1.
    """
    if prizes_count < 1:
        raise ValueError("prizes_count must be at least 1")
    if tickets_count < 1:
        raise ValueError("tickets_count must be at least 1")
    return max(1, (prizes_count + tickets_count - 1) // tickets_count)


def is_ticket_at_cap(used: int, cap: int) -> bool:
    return used >= cap


def choose_ticket(
    tickets: Sequence[int],
    used_counts: dict[int, int],
    cap: int,
    rng,
) -> int:
    eligible = [
        ticket for ticket in tickets if not is_ticket_at_cap(used_counts.get(ticket, 0), cap)
    ]
    if not eligible:
        raise ValueError("no eligible tickets")
    return rng.choice(eligible)
