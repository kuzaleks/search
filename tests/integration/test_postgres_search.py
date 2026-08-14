import os
import unittest
from collections.abc import AsyncIterator
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import cast, func, text
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.chunking import chunk_text
from app.config import EMBEDDING_DIMENSIONS, get_settings
from app.database import get_session
from app.embeddings import get_embedding_provider
from app.main import app
from app.models import Client, Document, DocumentChunk
from app.search import (
    hybrid_search_documents,
    search_clients,
    search_lexical_documents,
    search_semantic_documents,
)


POSTGRES_TESTS_ENABLED = os.getenv("RUN_POSTGRES_TESTS") == "1"


def embedding(dimension: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[dimension] = 1.0
    return vector


class FakeEmbeddingProvider:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.vector for _ in texts]


@unittest.skipUnless(
    POSTGRES_TESTS_ENABLED,
    "set RUN_POSTGRES_TESTS=1 to run PostgreSQL integration tests",
)
class PostgreSQLSearchTests(unittest.IsolatedAsyncioTestCase):
    engine: AsyncEngine
    connection: AsyncConnection
    session: AsyncSession

    async def asyncSetUp(self) -> None:
        settings = get_settings()
        if settings.environment == "production":
            self.fail("PostgreSQL integration tests cannot use production")

        self.engine = create_async_engine(
            settings.database_url,
            poolclass=NullPool,
        )
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.session = AsyncSession(
            bind=self.connection,
            expire_on_commit=False,
        )

    async def asyncTearDown(self) -> None:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_embedding_provider, None)
        await self.session.close()
        if self.transaction.is_active:
            await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    async def add_client(
        self,
        first_name: str = "Aurelius",
        last_name: str = "Quenford",
    ) -> Client:
        client = Client(
            first_name=first_name,
            last_name=last_name,
            email=f"integration-{uuid4().hex}@tests.nevis.dev",
            description="Integration search fixture",
            social_links=[],
        )
        self.session.add(client)
        await self.session.flush()
        return client

    async def add_document(
        self,
        client: Client,
        title: str,
        content: str,
        vectors: list[list[float]] | None = None,
    ) -> Document:
        chunks = chunk_text(content)
        chunk_vectors = vectors or [embedding(498) for _ in chunks]
        self.assertEqual(len(chunks), len(chunk_vectors))

        document = Document(
            client_id=client.id,
            title=title,
            content=content,
            chunks=[
                DocumentChunk(
                    chunk_index=index,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                    embedding=chunk_vector,
                    search_vector=func.to_tsvector(
                        cast("english", REGCONFIG),
                        chunk.text,
                    ),
                )
                for index, (chunk, chunk_vector) in enumerate(
                    zip(chunks, chunk_vectors, strict=True)
                )
            ],
        )
        self.session.add(document)
        await self.session.flush()
        return document

    async def test_required_extensions_and_indexes_exist(self) -> None:
        extensions = set(
            (
                await self.session.execute(
                    text("SELECT extname FROM pg_extension")
                )
            ).scalars()
        )
        indexes = set(
            (
                await self.session.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = 'public'"
                    )
                )
            ).scalars()
        )

        self.assertTrue({"pg_trgm", "vector"}.issubset(extensions))
        self.assertTrue(
            {
                "ix_documents_title_search_vector",
                "ix_documents_title_trgm",
                "ix_document_chunks_search_vector",
                "ix_document_chunks_embedding_hnsw",
            }.issubset(indexes)
        )

    async def test_client_exact_and_typo_search(self) -> None:
        client = await self.add_client()

        exact = await search_clients(
            self.session,
            "Aurelius Quenford",
            limit=10,
        )
        typo = await search_clients(
            self.session,
            "Aurelus Quenford",
            limit=10,
        )

        self.assertEqual(exact[0].client.id, client.id)
        self.assertIn(client.id, {match.client.id for match in typo})

    async def test_title_fts_stemming_and_typo_search(self) -> None:
        client = await self.add_client()
        document = await self.add_document(
            client,
            title="Celestine Fiduciary Mandate",
            content="Signed administrative record for a private account.",
        )

        stemmed = await search_lexical_documents(
            self.session,
            "Celestine fiduciary mandates",
            candidate_limit=100,
        )
        typo = await search_lexical_documents(
            self.session,
            "Celestin fiducary mandate",
            candidate_limit=100,
        )

        self.assertIn(document.id, {match.document.id for match in stemmed})
        self.assertIn(document.id, {match.document.id for match in typo})

    async def test_content_stemming_and_lexical_snippet(self) -> None:
        client = await self.add_client()
        document = await self.add_document(
            client,
            title="Laboratory Archive",
            content=(
                "The cryogenic observatory calibrates spectrometers and "
                "records photometric measurements."
            ),
        )

        matches = await search_lexical_documents(
            self.session,
            "calibrating spectrometer",
            candidate_limit=100,
        )

        match = next(
            item for item in matches if item.document.id == document.id
        )
        self.assertIn("calibrates spectrometers", match.snippet)

    async def test_content_snippet_is_preferred_over_title_snippet(self) -> None:
        client = await self.add_client()
        document = await self.add_document(
            client,
            title="Photometric Custody Record",
            content=(
                "Administrative introduction. Photometric custody evidence "
                "appears in this matching passage."
            ),
        )

        matches = await search_lexical_documents(
            self.session,
            "photometric custody",
            candidate_limit=100,
        )

        match = next(
            item for item in matches if item.document.id == document.id
        )
        self.assertIn("Photometric custody evidence", match.snippet)

    async def test_web_phrase_and_exclusion(self) -> None:
        client = await self.add_client()
        included = await self.add_document(
            client,
            title="Orbital Archive",
            content="The lunar mineral register contains orbital samples.",
        )
        excluded = await self.add_document(
            client,
            title="Insured Orbital Archive",
            content=(
                "The lunar mineral register contains insurance records."
            ),
        )

        matches = await search_lexical_documents(
            self.session,
            '"lunar mineral" -insurance',
            candidate_limit=100,
        )
        document_ids = {match.document.id for match in matches}

        self.assertIn(included.id, document_ids)
        self.assertNotIn(excluded.id, document_ids)

    async def test_semantic_search_selects_nearest_chunk_and_deduplicates(
        self,
    ) -> None:
        client = await self.add_client()
        content = " ".join(f"signal{index}" for index in range(500))
        document = await self.add_document(
            client,
            title="Signal Analysis",
            content=content,
            vectors=[embedding(500), embedding(500)],
        )

        matches = await search_semantic_documents(
            self.session,
            embedding(500),
            candidate_limit=100,
        )

        self.assertEqual(matches[0].document.id, document.id)
        self.assertEqual(
            sum(match.document.id == document.id for match in matches),
            1,
        )
        self.assertLessEqual(len(matches[0].snippet), 323)

    async def test_hybrid_search_rewards_both_retrieval_channels(self) -> None:
        client = await self.add_client()
        hybrid_document = await self.add_document(
            client,
            title="Neutrino Custody Memorandum",
            content="A unique neutrino custody account memorandum.",
            vectors=[embedding(501)],
        )
        await self.add_document(
            client,
            title="Unrelated Semantic Record",
            content="A record with no matching lexical terminology.",
            vectors=[embedding(501)],
        )

        matches = await hybrid_search_documents(
            self.session,
            "neutrino custody",
            embedding(501),
            limit=10,
        )

        self.assertEqual(matches[0].document.id, hybrid_document.id)

    async def test_lexical_no_match_returns_empty_collection(self) -> None:
        matches = await search_lexical_documents(
            self.session,
            "zzqvtermwithnomatch",
            candidate_limit=100,
        )

        self.assertEqual(matches, [])

    async def test_search_endpoint_uses_postgres_and_response_schema(
        self,
    ) -> None:
        client = await self.add_client()
        document = await self.add_document(
            client,
            title="Helios Compliance Dossier",
            content="A photometric compliance audit for the Helios account.",
            vectors=[embedding(499)],
        )

        async def override_session() -> AsyncIterator[AsyncSession]:
            yield self.session

        app.dependency_overrides[get_session] = override_session
        app.dependency_overrides[get_embedding_provider] = lambda: (
            FakeEmbeddingProvider(embedding(499))
        )

        async with AsyncClient(
            transport=ASGITransport(
                app=app,
                raise_app_exceptions=False,
            ),
            base_url="http://test",
        ) as client_http:
            response = await client_http.get(
                "/search",
                params={"q": "Helios compliance", "limit": 10},
            )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["query"], "Helios compliance")
        self.assertIn(
            str(document.id),
            {result["document"]["id"] for result in body["documents"]},
        )


if __name__ == "__main__":
    unittest.main()
