"""add index on audit_logs.timestamp

Revision ID: 3a6cb1fbc969
Revises: a7e2f5c9b1d3
Create Date: 2026-11-02
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3a6cb1fbc969"
down_revision: str | Sequence[str] | None = "a7e2f5c9b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Index audit timestamps for time-window queries (alerts, filters)."""
    op.create_index(
        op.f("ix_audit_logs_timestamp"),
        "audit_logs",
        ["timestamp"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the audit timestamp index."""
    op.drop_index(
        op.f("ix_audit_logs_timestamp"),
        table_name="audit_logs",
    )
