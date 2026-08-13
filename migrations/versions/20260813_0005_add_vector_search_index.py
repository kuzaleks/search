"""Add document embedding search index.

Revision ID: 20260813_0005
Revises: 20260813_0004
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260813_0005"
down_revision: str | Sequence[str] | None = "20260813_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_document_chunks_embedding_hnsw",
        "document_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_chunks_embedding_hnsw",
        table_name="document_chunks",
        postgresql_using="hnsw",
    )
