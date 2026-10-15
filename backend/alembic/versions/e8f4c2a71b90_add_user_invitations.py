"""add user invitations table

Revision ID: e8f4c2a71b90
Revises: c3f2b1e4d9a0
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8f4c2a71b90"
down_revision: str | Sequence[str] | None = "c3f2b1e4d9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the user invitations table."""
    op.create_table(
        "user_invitations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        op.f("ix_user_invitations_user_id"),
        "user_invitations",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the user invitations table."""
    op.drop_index(
        op.f("ix_user_invitations_user_id"),
        table_name="user_invitations",
    )
    op.drop_table("user_invitations")
