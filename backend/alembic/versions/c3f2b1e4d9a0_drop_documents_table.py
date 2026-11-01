"""Preserve the documents table after switching to file-based storage.

Revision ID: c3f2b1e4d9a0
Revises: 1a94d418aa98
Create Date: 2026-10-10 09:00:00.000000

This revision is intentionally a no-op. The documents table must not be
dropped automatically because it may still exist in deployed databases.
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "c3f2b1e4d9a0"
down_revision: str | Sequence[str] | None = "1a94d418aa98"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Keep the existing documents table intact."""


def downgrade() -> None:
    """No schema change was made by this revision."""
