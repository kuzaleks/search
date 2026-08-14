"""Add client trigram search indexes.

Revision ID: 20260814_0007
Revises: 20260813_0006
Create Date: 2026-08-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260814_0007"
down_revision: str | Sequence[str] | None = "20260813_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    full_name = sa.literal_column(
        "lower(first_name || ' ' || last_name)"
    ).label("full_name")
    email = sa.literal_column("lower(email)").label("email_lower")
    description = sa.literal_column(
        "lower(coalesce(description, ''))"
    ).label("description_lower")

    op.create_index(
        "ix_clients_full_name_trgm",
        "clients",
        [full_name],
        postgresql_using="gin",
        postgresql_ops={"full_name": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_clients_email_trgm",
        "clients",
        [email],
        postgresql_using="gin",
        postgresql_ops={"email_lower": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_clients_description_trgm",
        "clients",
        [description],
        postgresql_using="gin",
        postgresql_ops={"description_lower": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_clients_description_trgm", table_name="clients")
    op.drop_index("ix_clients_email_trgm", table_name="clients")
    op.drop_index("ix_clients_full_name_trgm", table_name="clients")
