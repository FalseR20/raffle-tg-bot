"""Domain enumerations shared by the ORM and services."""

from __future__ import annotations

from enum import StrEnum


class RoleName(StrEnum):
    ADMIN = "admin"
    ORGANIZER = "organizer"
    ORG_REP = "org_rep"


class InviteRole(StrEnum):
    ORGANIZER = "organizer"
    ORG_REP = "org_rep"


class RaffleStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    CLOSED = "closed"
    FINISHED = "finished"
    ARCHIVED = "archived"

    @property
    def is_current(self) -> bool:
        return self in (RaffleStatus.ACTIVE, RaffleStatus.CLOSED)

    @property
    def is_finished(self) -> bool:
        return self in (RaffleStatus.FINISHED, RaffleStatus.ARCHIVED)


class ApplicationStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_RECEIPT = "awaiting_receipt"
    PENDING_REVIEW = "pending_review"
    REVISION_REQUESTED = "revision_requested"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    VOIDED = "voided"

    @property
    def is_open(self) -> bool:
        """Statuses that still hold an unfinished participant flow."""
        return self in (
            ApplicationStatus.DRAFT,
            ApplicationStatus.AWAITING_RECEIPT,
            ApplicationStatus.PENDING_REVIEW,
            ApplicationStatus.REVISION_REQUESTED,
        )

    @property
    def is_reviewable(self) -> bool:
        return self in (
            ApplicationStatus.PENDING_REVIEW,
            ApplicationStatus.REVISION_REQUESTED,
        )

    @property
    def is_cancellable_by_user(self) -> bool:
        return self in (
            ApplicationStatus.DRAFT,
            ApplicationStatus.AWAITING_RECEIPT,
            ApplicationStatus.PENDING_REVIEW,
            ApplicationStatus.REVISION_REQUESTED,
        )


class ReceiptType(StrEnum):
    PHOTO = "photo"
    PDF = "pdf"
    IMAGE = "image"
