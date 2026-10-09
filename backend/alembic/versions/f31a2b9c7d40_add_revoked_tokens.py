"""Add persistent token revocation."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f31a2b9c7d40"
down_revision: str | Sequence[str] | None = "b29c84a3150e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.String(length=36), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("revoked_tokens")
