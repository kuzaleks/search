"""Create clients table.

Revision ID: 20260813_0002
Revises: 20260812_0001
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260813_0002"
down_revision: str | Sequence[str] | None = "20260812_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "social_links",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_clients_email_lower",
        "clients",
        [sa.text("lower(email)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_clients_email_lower", table_name="clients")
    op.drop_table("clients")
