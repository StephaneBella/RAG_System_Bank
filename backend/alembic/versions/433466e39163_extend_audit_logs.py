"""extend audit_logs with departments, response time and details

Revision ID: 433466e39163
Revises: 3a6cb1fbc969
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "433466e39163"
down_revision: str | Sequence[str] | None = "3a6cb1fbc969"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable columns only: existing rows are kept unchanged (NULL)."""
    op.add_column("audit_logs", sa.Column("user_department_id", sa.Integer(), nullable=True))
    op.add_column("audit_logs", sa.Column("resource_department_id", sa.Integer(), nullable=True))
    op.add_column("audit_logs", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("audit_logs", sa.Column("details", sa.JSON(), nullable=True))
    op.create_foreign_key(
        "audit_logs_user_department_id_fkey",
        "audit_logs",
        "departments",
        ["user_department_id"],
        ["id"],
    )
    op.create_foreign_key(
        "audit_logs_resource_department_id_fkey",
        "audit_logs",
        "departments",
        ["resource_department_id"],
        ["id"],
    )


def downgrade() -> None:
    """Drop the added columns and their foreign keys."""
    op.drop_constraint("audit_logs_resource_department_id_fkey", "audit_logs", type_="foreignkey")
    op.drop_constraint("audit_logs_user_department_id_fkey", "audit_logs", type_="foreignkey")
    op.drop_column("audit_logs", "details")
    op.drop_column("audit_logs", "duration_ms")
    op.drop_column("audit_logs", "resource_department_id")
    op.drop_column("audit_logs", "user_department_id")
