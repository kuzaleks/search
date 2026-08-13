import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
from app.search import search_clients, search_documents


logger = logging.getLogger(__name__)
router = APIRouter()
DatabaseSession = Annotated[AsyncSession, Depends(get_session)]
EmbeddingProvider = Annotated[
    OpenAIEmbeddingProvider,
    Depends(get_embedding_provider),
]
SearchQuery = Annotated[str, Query(min_length=1, max_length=500)]
SearchLimit = Annotated[int, Query(ge=1, le=50)]


async def generate_embeddings(
    embedding_provider: OpenAIEmbeddingProvider,
    texts: list[str],
) -> list[list[float]]:
    try:
        return await embedding_provider.embed(texts)
    except EmbeddingConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Embedding provider is not configured",
        ) from error
    except EmbeddingProviderError as error:
        logger.warning("Embedding generation failed: %s", error)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Embedding generation failed",
        ) from error


@router.post(
    "/clients",
    response_model=ClientResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "A client with this email already exists"
        }
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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A client with this email already exists",
        ) from error

    return client


@router.post(
    "/clients/{client_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Client not found"},
        status.HTTP_502_BAD_GATEWAY: {
            "description": "Embedding provider failed"
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Embedding provider is not configured"
        },
    },
    tags=["documents"],
)
async def create_document(
    client_id: UUID,
    document_data: DocumentCreate,
    session: DatabaseSession,
    embedding_provider: EmbeddingProvider,
) -> Document:
    if await session.get(Client, client_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client not found",
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
    responses={
        status.HTTP_502_BAD_GATEWAY: {
            "description": "Embedding provider failed"
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Embedding provider is not configured"
        },
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
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Search query must not be blank",
        )

    client_matches = await search_clients(session, query, limit)
    query_embedding = (
        await generate_embeddings(embedding_provider, [query])
    )[0]
    document_matches = await search_documents(
        session,
        query_embedding,
        limit,
    )

    return SearchResponse(
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
