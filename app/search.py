from dataclasses import dataclass

from sqlalchemy import case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Client, Document, DocumentChunk


CLIENT_MATCH_THRESHOLD = 0.20
DOCUMENT_CANDIDATE_MULTIPLIER = 10
MINIMUM_DOCUMENT_CANDIDATES = 100


@dataclass(frozen=True, slots=True)
class ClientMatch:
    client: Client
    score: float


@dataclass(frozen=True, slots=True)
class DocumentMatch:
    document: Document
    score: float
    snippet: str


async def search_clients(
    session: AsyncSession,
    query: str,
    limit: int,
) -> list[ClientMatch]:
    query_text = query.lower()
    full_name = func.concat_ws(" ", Client.first_name, Client.last_name)
    searchable_text = func.lower(
        func.concat_ws(
            " ",
            Client.first_name,
            Client.last_name,
            Client.email,
            func.coalesce(Client.description, ""),
        )
    )
    exact_match = or_(
        func.lower(Client.first_name) == query_text,
        func.lower(Client.last_name) == query_text,
        func.lower(full_name) == query_text,
        func.lower(Client.email) == query_text,
    )
    substring_match = func.strpos(searchable_text, query_text) > 0
    trigram_score = func.greatest(
        func.similarity(func.lower(Client.first_name), query_text),
        func.similarity(func.lower(Client.last_name), query_text),
        func.similarity(func.lower(full_name), query_text),
        func.similarity(func.lower(Client.email), query_text),
        func.word_similarity(query_text, searchable_text),
    )
    score = func.greatest(
        case((exact_match, literal(1.0)), else_=literal(0.0)),
        case((substring_match, literal(0.85)), else_=literal(0.0)),
        trigram_score,
    ).label("score")

    statement = (
        select(Client, score)
        .where(
            or_(
                exact_match,
                substring_match,
                trigram_score >= CLIENT_MATCH_THRESHOLD,
            )
        )
        .order_by(score.desc(), Client.id)
        .limit(limit)
    )
    rows = (await session.execute(statement)).all()

    return [
        ClientMatch(client=client, score=float(match_score))
        for client, match_score in rows
    ]


async def search_documents(
    session: AsyncSession,
    query_embedding: list[float],
    limit: int,
) -> list[DocumentMatch]:
    distance = DocumentChunk.embedding.cosine_distance(query_embedding)
    candidate_limit = max(
        MINIMUM_DOCUMENT_CANDIDATES,
        limit * DOCUMENT_CANDIDATE_MULTIPLIER,
    )
    candidates = (
        select(
            DocumentChunk.document_id,
            DocumentChunk.start_offset,
            DocumentChunk.end_offset,
            distance.label("distance"),
        )
        .order_by(distance)
        .limit(candidate_limit)
        .subquery()
    )
    statement = (
        select(
            Document,
            candidates.c.distance,
            candidates.c.start_offset,
            candidates.c.end_offset,
        )
        .join(candidates, candidates.c.document_id == Document.id)
        .order_by(candidates.c.distance, Document.id)
    )
    rows = (await session.execute(statement)).all()

    matches = []
    seen_document_ids = set()
    for document, match_distance, start_offset, end_offset in rows:
        if document.id in seen_document_ids:
            continue

        seen_document_ids.add(document.id)
        matches.append(
            DocumentMatch(
                document=document,
                score=1.0 - float(match_distance),
                snippet=document.content[start_offset:end_offset],
            )
        )
        if len(matches) == limit:
            break

    return matches
