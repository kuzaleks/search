from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import case, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Client, Document, DocumentChunk


CLIENT_MATCH_THRESHOLD = 0.20
DOCUMENT_SEMANTIC_THRESHOLD = 0.25
DOCUMENT_CANDIDATE_MULTIPLIER = 10
MINIMUM_DOCUMENT_CANDIDATES = 100
RRF_K = 60
SNIPPET_MAX_CHARACTERS = 320


@dataclass(frozen=True, slots=True)
class ClientMatch:
    client: Client
    score: float


@dataclass(frozen=True, slots=True)
class DocumentMatch:
    document: Document
    score: float
    snippet: str


def make_snippet(
    text: str,
    query: str | None = None,
    max_characters: int = SNIPPET_MAX_CHARACTERS,
) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_characters:
        return normalized

    match_position = 0
    if query:
        normalized_lower = normalized.lower()
        query_terms = [term for term in query.lower().split() if len(term) > 2]
        positions = [
            normalized_lower.find(term)
            for term in query_terms
            if normalized_lower.find(term) >= 0
        ]
        if positions:
            match_position = min(positions)

    start = max(0, match_position - max_characters // 3)
    if start:
        next_space = normalized.find(" ", start)
        start = next_space + 1 if next_space >= 0 else start

    end = min(len(normalized), start + max_characters)
    if end < len(normalized):
        last_space = normalized.rfind(" ", start, end)
        end = last_space if last_space > start else end

    return (
        ("..." if start else "")
        + normalized[start:end]
        + ("..." if end < len(normalized) else "")
    )


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


async def search_semantic_documents(
    session: AsyncSession,
    query_embedding: list[float],
    candidate_limit: int,
) -> list[DocumentMatch]:
    distance = DocumentChunk.embedding.cosine_distance(query_embedding)
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

        score = 1.0 - float(match_distance)
        if score < DOCUMENT_SEMANTIC_THRESHOLD:
            continue

        seen_document_ids.add(document.id)
        matches.append(
            DocumentMatch(
                document=document,
                score=score,
                snippet=make_snippet(
                    document.content[start_offset:end_offset]
                ),
            )
        )

    return matches


async def search_lexical_documents(
    session: AsyncSession,
    query: str,
    candidate_limit: int,
) -> list[DocumentMatch]:
    text_config = cast("english", REGCONFIG)
    ts_query = func.websearch_to_tsquery(text_config, query)
    title_match = Document.title_search_vector.op("@@")(ts_query)
    chunk_match = DocumentChunk.search_vector.op("@@")(ts_query)
    title_substring = func.strpos(func.lower(Document.title), query.lower()) > 0
    title_word_match = literal(query).op("<%")(Document.title)
    title_word_similarity = func.word_similarity(
        query,
        Document.title,
    )
    title_rank = 2 * func.ts_rank_cd(
        Document.title_search_vector,
        ts_query,
        32,
    )
    chunk_rank = func.ts_rank_cd(
        DocumentChunk.search_vector,
        ts_query,
        32,
    )
    score = func.greatest(
        title_rank,
        chunk_rank,
        case((title_substring, literal(0.9)), else_=literal(0.0)),
        title_word_similarity,
    ).label("score")
    chunk_text = func.substring(
        Document.content,
        DocumentChunk.start_offset + 1,
        DocumentChunk.end_offset - DocumentChunk.start_offset,
    ).label("chunk_text")
    best_chunks = (
        select(
            Document.id.label("document_id"),
            score,
            chunk_text,
        )
        .join(DocumentChunk, DocumentChunk.document_id == Document.id)
        .where(
            or_(
                title_match,
                chunk_match,
                title_substring,
                title_word_match,
            )
        )
        .distinct(Document.id)
        .order_by(Document.id, score.desc(), DocumentChunk.chunk_index)
        .subquery()
    )
    statement = (
        select(Document, best_chunks.c.score, best_chunks.c.chunk_text)
        .join(best_chunks, best_chunks.c.document_id == Document.id)
        .order_by(best_chunks.c.score.desc(), Document.id)
        .limit(candidate_limit)
    )
    rows = (await session.execute(statement)).all()

    matches = []
    seen_document_ids = set()
    for document, match_score, matched_text in rows:
        if document.id in seen_document_ids:
            continue

        seen_document_ids.add(document.id)
        matches.append(
            DocumentMatch(
                document=document,
                score=float(match_score),
                snippet=make_snippet(matched_text, query),
            )
        )

    return matches


def fuse_document_matches(
    semantic_matches: list[DocumentMatch],
    lexical_matches: list[DocumentMatch],
    limit: int,
) -> list[DocumentMatch]:
    scores: dict[UUID, float] = {}
    matches_by_id = {match.document.id: match for match in semantic_matches}

    for ranking in (semantic_matches, lexical_matches):
        for rank, match in enumerate(ranking, start=1):
            scores[match.document.id] = scores.get(match.document.id, 0.0) + (
                1.0 / (RRF_K + rank)
            )

    # Prefer a lexical snippet because it is centered around a matching term.
    matches_by_id.update(
        {match.document.id: match for match in lexical_matches}
    )
    maximum_score = 2.0 / (RRF_K + 1)
    ranked_ids = sorted(
        scores,
        key=lambda document_id: (-scores[document_id], str(document_id)),
    )[:limit]

    return [
        DocumentMatch(
            document=matches_by_id[document_id].document,
            score=scores[document_id] / maximum_score,
            snippet=matches_by_id[document_id].snippet,
        )
        for document_id in ranked_ids
    ]


async def hybrid_search_documents(
    session: AsyncSession,
    query: str,
    query_embedding: list[float],
    limit: int,
) -> list[DocumentMatch]:
    candidate_limit = max(
        MINIMUM_DOCUMENT_CANDIDATES,
        limit * DOCUMENT_CANDIDATE_MULTIPLIER,
    )
    semantic_matches = await search_semantic_documents(
        session,
        query_embedding,
        candidate_limit,
    )
    lexical_matches = await search_lexical_documents(
        session,
        query,
        candidate_limit,
    )

    return fuse_document_matches(
        semantic_matches,
        lexical_matches,
        limit,
    )
