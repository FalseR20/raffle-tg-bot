"""unique active draft per participant and raffle

Revision ID: 0002_unique_active_draft
Revises: 5fcdcf2bd325
Create Date: 2026-10-21 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_unique_active_draft"
down_revision: str | None = "5fcdcf2bd325"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_applications_active_draft",
        "applications",
        ["raffle_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('draft', 'awaiting_receipt')"),
    )


def downgrade() -> None:
    op.drop_index("uq_applications_active_draft", table_name="applications")
