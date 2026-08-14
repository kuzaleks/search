from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import (
    case,
    cast,
    func,
    literal,
    literal_column,
    or_,
    select,
    union_all,
)
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Client, Document, DocumentChunk


CLIENT_MATCH_THRESHOLD = 0.20
CLIENT_CANDIDATE_MULTIPLIER = 10
MINIMUM_CLIENT_CANDIDATES = 100
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
    email = func.lower(Client.email)
    exact_email_rows = (
        await session.execute(
            select(Client)
            .where(email == query_text)
            .limit(1)
        )
    ).all()
    if exact_email_rows:
        return [ClientMatch(client=exact_email_rows[0][0], score=1.0)]

    await session.execute(
        select(
            func.set_config(
                "pg_trgm.similarity_threshold",
                str(CLIENT_MATCH_THRESHOLD),
                True,
            ),
            func.set_config(
                "pg_trgm.word_similarity_threshold",
                str(CLIENT_MATCH_THRESHOLD),
                True,
            ),
        )
    )
    candidate_limit = max(
        MINIMUM_CLIENT_CANDIDATES,
        limit * CLIENT_CANDIDATE_MULTIPLIER,
    )
    full_name = func.concat_ws(" ", Client.first_name, Client.last_name)
    normalized_full_name = func.lower(
        Client.first_name
        + literal_column("' '")
        + Client.last_name
    )
    description = func.lower(
        func.coalesce(Client.description, literal_column("''"))
    )
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
        email == query_text,
    )
    substring_match = func.strpos(searchable_text, query_text) > 0
    trigram_score = func.greatest(
        func.similarity(func.lower(Client.first_name), query_text),
        func.similarity(func.lower(Client.last_name), query_text),
        func.similarity(func.lower(full_name), query_text),
        func.similarity(email, query_text),
        func.word_similarity(query_text, searchable_text),
    )
    score = func.greatest(
        case((exact_match, literal(1.0)), else_=literal(0.0)),
        case((substring_match, literal(0.85)), else_=literal(0.0)),
        trigram_score,
    ).label("score")

    substring_pattern = f"%{_escape_like(query_text)}%"
    full_name_candidate_score = func.greatest(
        func.similarity(normalized_full_name, query_text),
        func.word_similarity(query_text, normalized_full_name),
    )
    email_candidate_score = func.greatest(
        func.similarity(email, query_text),
        func.word_similarity(query_text, email),
    )

    def candidates(
        condition,
        candidate_score,
    ):
        return (
            select(
                Client.id.label("client_id"),
                candidate_score.label("candidate_score"),
            )
            .where(condition)
            .order_by(candidate_score.desc(), Client.id)
            .limit(candidate_limit)
        )

    candidate_rows = union_all(
        candidates(
            normalized_full_name.ilike(substring_pattern, escape="\\"),
            literal(0.85),
        ),
        candidates(
            email.ilike(substring_pattern, escape="\\"),
            literal(0.85),
        ),
        candidates(
            description.ilike(substring_pattern, escape="\\"),
            literal(0.85),
        ),
        candidates(
            or_(
                normalized_full_name.op("%")(query_text),
                normalized_full_name.op("%>")(query_text),
            ),
            full_name_candidate_score,
        ),
        candidates(
            or_(
                email.op("%")(query_text),
                email.op("%>")(query_text),
            ),
            email_candidate_score,
        ),
        candidates(
            description.op("%>")(query_text),
            func.word_similarity(query_text, description),
        ),
    ).subquery()
    best_candidates = (
        select(
            candidate_rows.c.client_id,
            func.max(candidate_rows.c.candidate_score).label(
                "candidate_score"
            ),
        )
        .group_by(candidate_rows.c.client_id)
        .order_by(
            func.max(candidate_rows.c.candidate_score).desc(),
            candidate_rows.c.client_id,
        )
        .limit(candidate_limit)
        .subquery()
    )
    statement = (
        select(Client, score)
        .join(
            best_candidates,
            best_candidates.c.client_id == Client.id,
        )
        .where(score >= CLIENT_MATCH_THRESHOLD)
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
    title_matches = await search_title_documents(
        session,
        query,
        candidate_limit,
    )
    chunk_matches = await search_chunk_documents(
        session,
        query,
        candidate_limit,
    )

    matches_by_id = {match.document.id: match for match in title_matches}
    for chunk_match in chunk_matches:
        title_match = matches_by_id.get(chunk_match.document.id)
        if title_match is None:
            matches_by_id[chunk_match.document.id] = chunk_match
            continue

        matches_by_id[chunk_match.document.id] = DocumentMatch(
            document=chunk_match.document,
            score=max(title_match.score, chunk_match.score),
            snippet=chunk_match.snippet,
        )

    return sorted(
        matches_by_id.values(),
        key=lambda match: (-match.score, str(match.document.id)),
    )[:candidate_limit]


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


async def search_title_documents(
    session: AsyncSession,
    query: str,
    candidate_limit: int,
) -> list[DocumentMatch]:
    text_config = cast("english", REGCONFIG)
    ts_query = func.websearch_to_tsquery(text_config, query)
    title_match = Document.title_search_vector.op("@@")(ts_query)
    title_substring = Document.title.ilike(
        f"%{_escape_like(query)}%",
        escape="\\",
    )
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
    title_candidates = union_all(
        select(
            Document.id.label("document_id"),
            title_rank.label("score"),
        )
        .where(title_match)
        .order_by(title_rank.desc(), Document.id)
        .limit(candidate_limit),
        select(
            Document.id.label("document_id"),
            literal(0.9).label("score"),
        )
        .where(title_substring)
        .order_by(Document.id)
        .limit(candidate_limit),
        select(
            Document.id.label("document_id"),
            title_word_similarity.label("score"),
        )
        .where(title_word_match)
        .order_by(title_word_similarity.desc(), Document.id)
        .limit(candidate_limit),
    ).subquery()
    best_titles = (
        select(
            title_candidates.c.document_id,
            func.max(title_candidates.c.score).label("score"),
        )
        .group_by(title_candidates.c.document_id)
        .order_by(
            func.max(title_candidates.c.score).desc(),
            title_candidates.c.document_id,
        )
        .limit(candidate_limit)
        .subquery()
    )
    statement = (
        select(Document, best_titles.c.score)
        .join(best_titles, best_titles.c.document_id == Document.id)
        .order_by(best_titles.c.score.desc(), Document.id)
    )
    rows = (await session.execute(statement)).all()

    return [
        DocumentMatch(
            document=document,
            score=float(match_score),
            snippet=make_snippet(document.content, query),
        )
        for document, match_score in rows
    ]


async def search_chunk_documents(
    session: AsyncSession,
    query: str,
    candidate_limit: int,
) -> list[DocumentMatch]:
    text_config = cast("english", REGCONFIG)
    ts_query = func.websearch_to_tsquery(text_config, query)
    chunk_match = DocumentChunk.search_vector.op("@@")(ts_query)
    chunk_rank = func.ts_rank_cd(
        DocumentChunk.search_vector,
        ts_query,
        32,
    ).label("score")
    best_chunks = (
        select(
            DocumentChunk.document_id,
            DocumentChunk.start_offset,
            DocumentChunk.end_offset,
            chunk_rank,
        )
        .where(chunk_match)
        .distinct(DocumentChunk.document_id)
        .order_by(
            DocumentChunk.document_id,
            chunk_rank.desc(),
            DocumentChunk.chunk_index,
        )
        .subquery()
    )
    statement = (
        select(
            Document,
            best_chunks.c.score,
            best_chunks.c.start_offset,
            best_chunks.c.end_offset,
        )
        .join(best_chunks, best_chunks.c.document_id == Document.id)
        .order_by(best_chunks.c.score.desc(), Document.id)
        .limit(candidate_limit)
    )
    rows = (await session.execute(statement)).all()

    return [
        DocumentMatch(
            document=document,
            score=float(match_score),
            snippet=make_snippet(
                document.content[start_offset:end_offset],
                query,
            ),
        )
        for document, match_score, start_offset, end_offset in rows
    ]


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
