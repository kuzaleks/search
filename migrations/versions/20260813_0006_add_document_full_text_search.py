"""Add document full-text search.

Revision ID: 20260813_0006
Revises: 20260813_0005
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260813_0006"
down_revision: str | Sequence[str] | None = "20260813_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "title_search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('english', coalesce(title, ''))",
                persisted=True,
            ),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_documents_title_search_vector",
        "documents",
        ["title_search_vector"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_documents_title_trgm",
        "documents",
        ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )

    op.add_column(
        "document_chunks",
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE document_chunks AS chunk "
            "SET search_vector = to_tsvector("
            "'english', substring(document.content "
            "FROM chunk.start_offset + 1 "
            "FOR chunk.end_offset - chunk.start_offset)) "
            "FROM documents AS document "
            "WHERE document.id = chunk.document_id"
        )
    )
    op.alter_column("document_chunks", "search_vector", nullable=False)
    op.create_index(
        "ix_document_chunks_search_vector",
        "document_chunks",
        ["search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_chunks_search_vector",
        table_name="document_chunks",
        postgresql_using="gin",
    )
    op.drop_column("document_chunks", "search_vector")
    op.drop_index(
        "ix_documents_title_trgm",
        table_name="documents",
        postgresql_using="gin",
    )
    op.drop_index(
        "ix_documents_title_search_vector",
        table_name="documents",
        postgresql_using="gin",
    )
    op.drop_column("documents", "title_search_vector")
