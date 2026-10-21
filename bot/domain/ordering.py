"""Deterministic per-user ordering helpers."""

from __future__ import annotations

import hashlib

from bot.db.models import Organization


def stable_hash(*parts: int) -> int:
    payload = ":".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def randomized_organizations(
    organizations: list[Organization], *, user_id: int, raffle_id: int
) -> list[Organization]:
    """Stable order for one user in one raffle, different across users."""
    if len(organizations) <= 1:
        return list(organizations)
    seed = stable_hash(user_id, raffle_id)

    def key(organization: Organization) -> int:
        return stable_hash(seed, organization.id)

    return sorted(organizations, key=key)
