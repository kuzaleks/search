import asyncio
import logging
from contextlib import suppress
from time import perf_counter
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy import cast, func
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.chunking import chunk_text
from app.database import get_session
from app.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    OpenAIEmbeddingProvider,
    get_embedding_provider,
)
from app.errors import APIError, documented_error
from app.models import Client, Document, DocumentChunk
from app.schemas import (
    ClientCreate,
    ClientResponse,
    ClientSearchResult,
    DocumentCreate,
    DocumentResponse,
    DocumentSearchResult,
    SearchResponse,
)
from app.search import (
    DocumentSearchTimings,
    fuse_document_matches,
    get_document_candidate_limit,
    search_clients,
    search_lexical_documents,
    search_semantic_documents,
)


logger = logging.getLogger(__name__)
router = APIRouter(
    responses={
        status.HTTP_500_INTERNAL_SERVER_ERROR: documented_error(
            "Unexpected server error",
            "internal_server_error",
            "An unexpected error occurred",
        )
    }
)
DatabaseSession = Annotated[AsyncSession, Depends(get_session)]
EmbeddingProvider = Annotated[
    OpenAIEmbeddingProvider,
    Depends(get_embedding_provider),
]
ClientId = Annotated[
    UUID,
    Path(description="Unique identifier of the client that owns the document"),
]
SearchQuery = Annotated[
    str,
    Query(
        min_length=1,
        max_length=500,
        description="Text used for client and hybrid document retrieval",
        examples=["address proof"],
    ),
]
SearchLimit = Annotated[
    int,
    Query(
        ge=1,
        le=50,
        description="Maximum results returned in each result collection",
    ),
]


async def generate_embeddings(
    embedding_provider: OpenAIEmbeddingProvider,
    texts: list[str],
) -> list[list[float]]:
    try:
        return await embedding_provider.embed(texts)
    except EmbeddingConfigurationError as error:
        raise APIError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="embedding_provider_not_configured",
            message="Embedding provider is not configured",
        ) from error
    except EmbeddingProviderError as error:
        logger.warning("Embedding generation failed: %s", error)
        raise APIError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="embedding_provider_error",
            message="Embedding generation failed",
        ) from error


@router.post(
    "/clients",
    response_model=ClientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a client",
    description=(
        "Creates a client with a case-insensitively unique email address. "
        "Email values are normalized to lowercase before storage."
    ),
    response_description="The newly created client",
    responses={
        status.HTTP_409_CONFLICT: documented_error(
            "Client email already exists",
            "client_already_exists",
            "A client with this email already exists",
        ),
        status.HTTP_422_UNPROCESSABLE_CONTENT: documented_error(
            "Invalid client data",
            "validation_error",
            "Request validation failed",
        ),
    },
    tags=["clients"],
)
async def create_client(
    client_data: ClientCreate,
    session: DatabaseSession,
) -> Client:
    client = Client(
        first_name=client_data.first_name,
        last_name=client_data.last_name,
        email=str(client_data.email).lower(),
        description=client_data.description,
        social_links=client_data.social_links,
    )
    session.add(client)

    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise APIError(
            status_code=status.HTTP_409_CONFLICT,
            code="client_already_exists",
            message="A client with this email already exists",
        ) from error

    return client


@router.post(
    "/clients/{client_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a document to a client",
    description=(
        "Splits the document into overlapping chunks, generates embeddings "
        "in one provider request, and stores the document atomically."
    ),
    response_description="The ingested document",
    responses={
        status.HTTP_404_NOT_FOUND: documented_error(
            "Client not found",
            "client_not_found",
            "Client not found",
        ),
        status.HTTP_422_UNPROCESSABLE_CONTENT: documented_error(
            "Invalid document or client identifier",
            "validation_error",
            "Request validation failed",
        ),
        status.HTTP_502_BAD_GATEWAY: documented_error(
            "Embedding provider request failed",
            "embedding_provider_error",
            "Embedding generation failed",
        ),
        status.HTTP_503_SERVICE_UNAVAILABLE: documented_error(
            "Embedding provider is not configured",
            "embedding_provider_not_configured",
            "Embedding provider is not configured",
        ),
    },
    tags=["documents"],
)
async def create_document(
    client_id: ClientId,
    document_data: DocumentCreate,
    session: DatabaseSession,
    embedding_provider: EmbeddingProvider,
) -> Document:
    if await session.get(Client, client_id) is None:
        raise APIError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="client_not_found",
            message="Client not found",
        )

    # End the read transaction before waiting on the external provider.
    await session.rollback()
    chunks = chunk_text(document_data.content)

    embeddings = await generate_embeddings(
        embedding_provider,
        [chunk.text for chunk in chunks],
    )

    document = Document(
        client_id=client_id,
        title=document_data.title,
        content=document_data.content,
        chunks=[
            DocumentChunk(
                chunk_index=index,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                embedding=embedding,
                search_vector=func.to_tsvector(
                    cast("english", REGCONFIG),
                    chunk.text,
                ),
            )
            for index, (chunk, embedding) in enumerate(
                zip(chunks, embeddings, strict=True)
            )
        ],
    )
    session.add(document)
    await session.commit()

    return document


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Search clients and documents",
    description=(
        "Returns separate client and document rankings. Client retrieval uses "
        "metadata matching; document retrieval combines full-text, trigram, "
        "and vector search with reciprocal-rank fusion."
    ),
    response_description="Separate relevance-ranked client and document lists",
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: documented_error(
            "Invalid search query or result limit",
            "validation_error",
            "Request validation failed",
        ),
        status.HTTP_502_BAD_GATEWAY: documented_error(
            "Embedding provider request failed",
            "embedding_provider_error",
            "Embedding generation failed",
        ),
        status.HTTP_503_SERVICE_UNAVAILABLE: documented_error(
            "Embedding provider is not configured",
            "embedding_provider_not_configured",
            "Embedding provider is not configured",
        ),
    },
    tags=["search"],
)
async def search(
    q: SearchQuery,
    session: DatabaseSession,
    embedding_provider: EmbeddingProvider,
    limit: SearchLimit = 10,
) -> SearchResponse:
    query = q.strip()
    if not query:
        raise APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation_error",
            message="Search query must not be blank",
        )

    request_started = perf_counter()

    async def generate_query_embedding() -> tuple[list[float], float]:
        phase_started = perf_counter()
        query_embedding = (
            await generate_embeddings(embedding_provider, [query])
        )[0]
        return query_embedding, (perf_counter() - phase_started) * 1_000

    embedding_task = asyncio.create_task(generate_query_embedding())
    query_embedding: list[float] | None = None
    embedding_ms = 0.0
    document_timings = DocumentSearchTimings()
    candidate_limit = get_document_candidate_limit(limit)
    try:
        await asyncio.sleep(0)
        if embedding_task.done():
            query_embedding, embedding_ms = embedding_task.result()

        phase_started = perf_counter()
        client_matches = await search_clients(session, query, limit)
        client_ms = (perf_counter() - phase_started) * 1_000

        phase_started = perf_counter()
        lexical_matches = await search_lexical_documents(
            session,
            query,
            candidate_limit,
        )
        document_timings.lexical_ms = (
            perf_counter() - phase_started
        ) * 1_000

        if query_embedding is None:
            query_embedding, embedding_ms = await embedding_task
    except BaseException:
        if not embedding_task.done():
            embedding_task.cancel()
            with suppress(asyncio.CancelledError):
                await embedding_task
        elif not embedding_task.cancelled():
            embedding_task.exception()
        raise

    assert query_embedding is not None
    phase_started = perf_counter()
    semantic_matches = await search_semantic_documents(
        session,
        query_embedding,
        candidate_limit,
    )
    document_timings.semantic_ms = (
        perf_counter() - phase_started
    ) * 1_000

    phase_started = perf_counter()
    document_matches = fuse_document_matches(
        semantic_matches,
        lexical_matches,
        limit,
    )
    document_timings.fusion_ms = (
        perf_counter() - phase_started
    ) * 1_000

    phase_started = perf_counter()
    response = SearchResponse(
        query=query,
        clients=[
            ClientSearchResult(score=match.score, client=match.client)
            for match in client_matches
        ],
        documents=[
            DocumentSearchResult(
                score=match.score,
                document=match.document,
                snippet=match.snippet,
            )
            for match in document_matches
        ],
    )
    response_build_ms = (perf_counter() - phase_started) * 1_000
    total_ms = (perf_counter() - request_started) * 1_000
    logger.info(
        "Search completed total_ms=%.1f embedding_ms=%.1f client_ms=%.1f "
        "semantic_ms=%.1f lexical_ms=%.1f fusion_ms=%.1f "
        "response_build_ms=%.1f clients=%d documents=%d",
        total_ms,
        embedding_ms,
        client_ms,
        document_timings.semantic_ms,
        document_timings.lexical_ms,
        document_timings.fusion_ms,
        response_build_ms,
        len(client_matches),
        len(document_matches),
    )
    return response
