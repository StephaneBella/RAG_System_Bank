"""Drop the vestigial documents table (storage is file-based now).

Revision ID: a7e2f5c9b1d3
Revises: d0a1c2e3f4b5
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7e2f5c9b1d3"
down_revision: str | Sequence[str] | None = "d0a1c2e3f4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the unused documents table left behind by file-based storage."""
    op.drop_table("documents")


def downgrade() -> None:
    """Recreate the documents table."""
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
