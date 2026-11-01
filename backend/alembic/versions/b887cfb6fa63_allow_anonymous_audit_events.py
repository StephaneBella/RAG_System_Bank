"""Allow anonymous audit events."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b887cfb6fa63"
down_revision: str | Sequence[str] | None = "f31a2b9c7d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow audit events without a known user or resource."""
    op.alter_column(
        "audit_logs",
        "user_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "audit_logs",
        "resource_id",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    """Restore the original non-null constraints."""
    op.alter_column(
        "audit_logs",
        "resource_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "audit_logs",
        "user_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
