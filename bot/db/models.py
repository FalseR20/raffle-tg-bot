"""SQLAlchemy models for the raffle bot.

Money is always ``Numeric`` and mapped to :class:`decimal.Decimal`.
Receipt binaries are never stored here, only object-storage metadata.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BIGINT,
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.db.base import Base
from bot.db.enums import (
    ApplicationStatus,
    InviteRole,
    RaffleStatus,
    ReceiptType,
    RoleName,
)


def _enum(enum_cls: type, name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        values_callable=lambda members: [str(member.value) for member in members],
        validate_strings=True,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(Text)
    first_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_name: Mapped[str | None] = mapped_column(Text)
    language_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    rules_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rules_version: Mapped[str | None] = mapped_column(Text)
    privacy_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    privacy_version: Mapped[str | None] = mapped_column(Text)
    age_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publish_name_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    roles: Mapped[list[Role]] = relationship(back_populates="user", lazy="selectin")


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint(
            "role <> 'org_rep' OR organization_id IS NOT NULL",
            name="org_rep_requires_organization",
        ),
        Index(
            "uq_roles_active",
            "user_id",
            "role",
            text("coalesce(organization_id, 0)"),
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_roles_user_active", "user_id", postgresql_where=text("revoked_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[RoleName] = mapped_column(_enum(RoleName, "role_name"), nullable=False)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    granted_by: Mapped[int | None] = mapped_column(BIGINT)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[int | None] = mapped_column(BIGINT)

    user: Mapped[User] = relationship(back_populates="roles")
    organization: Mapped[Organization | None] = relationship(back_populates="roles")


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (CheckConstraint("length(slug) > 0", name="slug_not_empty"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    logo_file_id: Mapped[str | None] = mapped_column(Text)
    review_chat_id: Mapped[int] = mapped_column(BIGINT, nullable=False)
    review_thread_id: Mapped[int | None] = mapped_column(BIGINT)
    contact_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    payment_methods: Mapped[list[PaymentMethod]] = relationship(
        back_populates="organization",
        order_by="PaymentMethod.sort_order",
        lazy="selectin",
    )
    roles: Mapped[list[Role]] = relationship(back_populates="organization", lazy="selectin")


class PaymentMethod(TimestampMixin, Base):
    __tablename__ = "payment_methods"
    __table_args__ = (
        CheckConstraint("length(title) BETWEEN 1 AND 64", name="title_length"),
        CheckConstraint("length(instructions_text) BETWEEN 1 AND 3000", name="instructions_length"),
        Index("ix_payment_methods_org_active_sort", "organization_id", "is_active", "sort_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    instructions_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    organization: Mapped[Organization] = relationship(back_populates="payment_methods")


class Raffle(TimestampMixin, Base):
    __tablename__ = "raffles"
    __table_args__ = (
        CheckConstraint("ticket_price > 0", name="ticket_price_positive"),
        CheckConstraint("min_amount >= ticket_price", name="min_amount_gte_ticket_price"),
        CheckConstraint("prizes_count >= 1", name="prizes_count_positive"),
        CheckConstraint("end_at > start_at", name="end_after_start"),
        CheckConstraint("last_ticket_number >= 0", name="last_ticket_non_negative"),
        CheckConstraint("currency = 'BYN'", name="currency_byn"),
        Index(
            "uq_raffles_current",
            text("(true)"),
            unique=True,
            postgresql_where=text("status IN ('active', 'closed')"),
        ),
        Index("ix_raffles_status_start", "status", "start_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rules_text: Mapped[str | None] = mapped_column(Text)
    ticket_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    min_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    prizes_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="BYN")
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[RaffleStatus] = mapped_column(
        _enum(RaffleStatus, "raffle_status"),
        nullable=False,
        default=RaffleStatus.DRAFT,
        server_default=RaffleStatus.DRAFT.value,
    )
    channel_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    channel_title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_ticket_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int] = mapped_column(BIGINT, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organizations: Mapped[list[RaffleOrganization]] = relationship(
        back_populates="raffle", lazy="selectin"
    )


class RaffleOrganization(Base):
    __tablename__ = "raffle_organizations"
    __table_args__ = (UniqueConstraint("raffle_id", "organization_id"),)

    raffle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raffles.id", ondelete="CASCADE"), primary_key=True
    )
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="RESTRICT"), primary_key=True
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    raffle: Mapped[Raffle] = relationship(back_populates="organizations")
    organization: Mapped[Organization] = relationship(lazy="selectin")


class Application(TimestampMixin, Base):
    __tablename__ = "applications"
    __table_args__ = (
        CheckConstraint("amount IS NULL OR amount > 0", name="amount_positive"),
        CheckConstraint(
            "status NOT IN "
            "('awaiting_receipt', 'pending_review', 'revision_requested', "
            "'confirmed', 'rejected', 'voided') "
            "OR amount IS NOT NULL",
            name="amount_required_after_amount_step",
        ),
        CheckConstraint(
            "status <> 'confirmed' OR ("
            "tickets_awarded >= 1 "
            "AND first_ticket_number IS NOT NULL "
            "AND last_ticket_number IS NOT NULL "
            "AND first_ticket_number <= last_ticket_number "
            "AND last_ticket_number - first_ticket_number + 1 = tickets_awarded)",
            name="confirmed_range_consistent",
        ),
        CheckConstraint(
            "status <> 'voided' OR ("
            "first_ticket_number IS NULL AND last_ticket_number IS NULL "
            "AND voided_first_ticket_number IS NOT NULL "
            "AND voided_last_ticket_number IS NOT NULL "
            "AND voided_last_ticket_number - voided_first_ticket_number + 1 "
            "= tickets_awarded)",
            name="voided_range_consistent",
        ),
        CheckConstraint(
            "status IN ('confirmed', 'voided') OR first_ticket_number IS NULL",
            name="ranges_only_when_confirmed",
        ),
        CheckConstraint("tickets_awarded >= 0", name="tickets_awarded_non_negative"),
        UniqueConstraint("raffle_id", "payment_code", name="uq_applications_raffle_payment_code"),
        Index("ix_applications_raffle_status", "raffle_id", "status"),
        Index("ix_applications_user", "user_id"),
        Index("ix_applications_org_status", "organization_id", "status"),
        Index(
            "uq_applications_active_first_ticket",
            "raffle_id",
            "first_ticket_number",
            unique=True,
            postgresql_where=text("first_ticket_number IS NOT NULL"),
        ),
        Index(
            "uq_applications_active_last_ticket",
            "raffle_id",
            "last_ticket_number",
            unique=True,
            postgresql_where=text("last_ticket_number IS NOT NULL"),
        ),
        Index(
            "uq_applications_receipt_file_unique_id",
            "receipt_file_unique_id",
            unique=True,
            postgresql_where=text("receipt_file_unique_id IS NOT NULL"),
        ),
        Index("ix_applications_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    public_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    payment_code: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    raffle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raffles.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    payment_method_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("payment_methods.id", ondelete="SET NULL")
    )
    payment_method_title: Mapped[str | None] = mapped_column(Text)
    payment_instructions_snapshot: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="BYN")
    tickets_awarded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tickets_total_after: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[ApplicationStatus] = mapped_column(
        _enum(ApplicationStatus, "application_status"),
        nullable=False,
        default=ApplicationStatus.DRAFT,
        server_default=ApplicationStatus.DRAFT.value,
    )
    receipt_type: Mapped[ReceiptType | None] = mapped_column(_enum(ReceiptType, "receipt_type"))
    receipt_file_id: Mapped[str | None] = mapped_column(Text)
    receipt_file_unique_id: Mapped[str | None] = mapped_column(Text)
    receipt_object_key: Mapped[str | None] = mapped_column(Text)
    receipt_etag: Mapped[str | None] = mapped_column(Text)
    receipt_mime: Mapped[str | None] = mapped_column(Text)
    receipt_size: Mapped[int | None] = mapped_column(Integer)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(BIGINT)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    first_ticket_number: Mapped[int | None] = mapped_column(Integer)
    last_ticket_number: Mapped[int | None] = mapped_column(Integer)
    voided_first_ticket_number: Mapped[int | None] = mapped_column(Integer)
    voided_last_ticket_number: Mapped[int | None] = mapped_column(Integer)
    card_chat_id: Mapped[int | None] = mapped_column(BIGINT)
    card_message_id: Mapped[int | None] = mapped_column(BIGINT)
    user_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notification_error: Mapped[str | None] = mapped_column(Text)

    raffle: Mapped[Raffle] = relationship(lazy="joined")
    user: Mapped[User] = relationship(lazy="joined")
    organization: Mapped[Organization] = relationship(lazy="joined")
    payment_method: Mapped[PaymentMethod | None] = relationship(lazy="joined")


class ParticipantTotal(Base):
    __tablename__ = "participant_totals"
    __table_args__ = (
        CheckConstraint("confirmed_amount >= 0", name="confirmed_amount_non_negative"),
        CheckConstraint("tickets_issued >= 0", name="tickets_issued_non_negative"),
    )

    raffle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raffles.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    confirmed_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    tickets_issued: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(lazy="joined")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str | None] = mapped_column(Text)
    application_id: Mapped[int | None] = mapped_column(
        BIGINT, ForeignKey("applications.id", ondelete="SET NULL")
    )
    raffle_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("raffles.id", ondelete="SET NULL")
    )
    actor_id: Mapped[int | None] = mapped_column(BIGINT)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    old_status: Mapped[str | None] = mapped_column(Text)
    new_status: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Invite(Base):
    __tablename__ = "invites"
    __table_args__ = (
        CheckConstraint(
            "role <> 'org_rep' OR organization_id IS NOT NULL",
            name="org_rep_invite_requires_organization",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    role: Mapped[InviteRole] = mapped_column(_enum(InviteRole, "invite_role"), nullable=False)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE")
    )
    created_by: Mapped[int] = mapped_column(BIGINT, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_by: Mapped[int | None] = mapped_column(BIGINT)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[int | None] = mapped_column(BIGINT)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    organization: Mapped[Organization | None] = relationship(lazy="joined")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(BIGINT)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Winner(Base):
    __tablename__ = "winners"
    __table_args__ = (
        CheckConstraint("place >= 1", name="place_positive"),
        UniqueConstraint("raffle_id", "place", name="uq_winners_raffle_place"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raffle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raffles.id", ondelete="CASCADE"), nullable=False
    )
    place: Mapped[int] = mapped_column(Integer, nullable=False)
    ticket_number: Mapped[int] = mapped_column(Integer, nullable=False)
    application_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False
    )
    announced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    application: Mapped[Application] = relationship(lazy="joined")
