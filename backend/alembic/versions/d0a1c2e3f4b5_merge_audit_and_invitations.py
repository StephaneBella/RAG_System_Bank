"""Merge the audit-event and user-invitation branches.

Revision ID: d0a1c2e3f4b5
Revises: b887cfb6fa63, e8f4c2a71b90
"""

from collections.abc import Sequence

revision: str = "d0a1c2e3f4b5"
down_revision: str | Sequence[str] | None = ("b887cfb6fa63", "e8f4c2a71b90")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge point: no schema changes."""


def downgrade() -> None:
    """Merge point: no schema changes."""
