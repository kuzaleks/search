"""Replace duplicated chunk text with document offsets.

Revision ID: 20260813_0004
Revises: 20260813_0003
Create Date: 2026-08-13
"""

import re
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_0004"
down_revision: str | Sequence[str] | None = "20260813_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHUNK_SIZE_WORDS = 400
CHUNK_OVERLAP_WORDS = 50


def chunk_offsets(text: str) -> list[tuple[int, int]]:
    words = list(re.finditer(r"\S+", text))
    offsets = []
    step = CHUNK_SIZE_WORDS - CHUNK_OVERLAP_WORDS

    for start in range(0, len(words), step):
        end = min(start + CHUNK_SIZE_WORDS, len(words))
        offsets.append((words[start].start(), words[end - 1].end()))
        if end == len(words):
            break

    return offsets


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column("start_offset", sa.Integer(), nullable=True),
    )
    op.add_column(
        "document_chunks",
        sa.Column("end_offset", sa.Integer(), nullable=True),
    )

    connection = op.get_bind()
    documents = connection.execute(
        sa.text("SELECT id, content FROM documents")
    ).mappings()

    for document in documents:
        offsets = chunk_offsets(document["content"])
        chunk_indexes = connection.execute(
            sa.text(
                "SELECT chunk_index FROM document_chunks "
                "WHERE document_id = :document_id ORDER BY chunk_index"
            ),
            {"document_id": document["id"]},
        ).scalars().all()
        if chunk_indexes != list(range(len(offsets))):
            raise RuntimeError(
                f"Unexpected chunks for document {document['id']}"
            )

        for chunk_index, (start_offset, end_offset) in enumerate(offsets):
            connection.execute(
                sa.text(
                    "UPDATE document_chunks "
                    "SET start_offset = :start_offset, end_offset = :end_offset "
                    "WHERE document_id = :document_id "
                    "AND chunk_index = :chunk_index"
                ),
                {
                    "document_id": document["id"],
                    "chunk_index": chunk_index,
                    "start_offset": start_offset,
                    "end_offset": end_offset,
                },
            )

    op.alter_column("document_chunks", "start_offset", nullable=False)
    op.alter_column("document_chunks", "end_offset", nullable=False)
    op.create_check_constraint(
        "ck_document_chunks_start_offset_nonnegative",
        "document_chunks",
        "start_offset >= 0",
    )
    op.create_check_constraint(
        "ck_document_chunks_offsets_ordered",
        "document_chunks",
        "end_offset > start_offset",
    )
    op.drop_column("document_chunks", "content")


def downgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column("content", sa.Text(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE document_chunks AS chunk "
            "SET content = substring(document.content "
            "FROM chunk.start_offset + 1 "
            "FOR chunk.end_offset - chunk.start_offset) "
            "FROM documents AS document "
            "WHERE document.id = chunk.document_id"
        )
    )
    op.alter_column("document_chunks", "content", nullable=False)
    op.drop_constraint(
        "ck_document_chunks_offsets_ordered",
        "document_chunks",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_chunks_start_offset_nonnegative",
        "document_chunks",
        type_="check",
    )
    op.drop_column("document_chunks", "end_offset")
    op.drop_column("document_chunks", "start_offset")
