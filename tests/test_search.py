import unittest
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException

from app.api import search
from app.config import EMBEDDING_DIMENSIONS
from app.embeddings import EmbeddingProviderError
from app.models import Client, Document
from app.search import search_clients, search_documents


class FakeResult:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows

    def all(self) -> list[tuple]:
        return self.rows


class FakeSession:
    def __init__(self, result_sets: list[list[tuple]]) -> None:
        self.result_sets = result_sets
        self.execute_count = 0

    async def execute(self, statement) -> FakeResult:
        rows = self.result_sets[self.execute_count]
        self.execute_count += 1
        return FakeResult(rows)


class FakeEmbeddingProvider:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.texts: list[str] | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts = texts
        if self.error:
            raise self.error
        return [[0.1] * EMBEDDING_DIMENSIONS for _ in texts]


def make_client() -> Client:
    return Client(
        id=uuid4(),
        first_name="John",
        last_name="Doe",
        email="john.doe@neviswealth.com",
        description="Wealth management client",
        social_links=[],
    )


def make_document(title: str, content: str) -> Document:
    return Document(
        id=uuid4(),
        client_id=uuid4(),
        title=title,
        content=content,
        created_at=datetime.now(UTC),
    )


class SearchServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_ranked_client_results(self) -> None:
        client = make_client()
        session = FakeSession([[(client, 0.85)]])

        matches = await search_clients(session, "NevisWealth", limit=10)

        self.assertEqual(len(matches), 1)
        self.assertIs(matches[0].client, client)
        self.assertEqual(matches[0].score, 0.85)

    async def test_document_search_deduplicates_and_reconstructs_snippet(
        self,
    ) -> None:
        first = make_document("Utility bill", "prefix address confirmation")
        second = make_document("Statement", "account statement content")
        session = FakeSession(
            [
                [
                    (first, 0.1, 7, 27),
                    (first, 0.2, 0, 6),
                    (second, 0.3, 0, 17),
                ]
            ]
        )

        matches = await search_documents(
            session,
            [0.1] * EMBEDDING_DIMENSIONS,
            limit=10,
        )

        self.assertEqual([match.document for match in matches], [first, second])
        self.assertAlmostEqual(matches[0].score, 0.9)
        self.assertEqual(matches[0].snippet, "address confirmation")
        self.assertEqual(matches[1].snippet, "account statement")

    async def test_search_returns_separate_ranked_collections(self) -> None:
        client = make_client()
        document = make_document("Utility bill", "address confirmation")
        session = FakeSession(
            [
                [(client, 0.85)],
                [(document, 0.1, 0, len(document.content))],
            ]
        )
        provider = FakeEmbeddingProvider()

        response = await search(
            q=" address proof ",
            session=session,
            embedding_provider=provider,
            limit=10,
        )

        self.assertEqual(response.query, "address proof")
        self.assertEqual(provider.texts, ["address proof"])
        self.assertEqual(response.clients[0].client.email, client.email)
        self.assertEqual(response.documents[0].document.title, "Utility bill")
        self.assertEqual(
            response.documents[0].snippet,
            "address confirmation",
        )

    async def test_blank_query_is_rejected_before_search(self) -> None:
        session = FakeSession([])
        provider = FakeEmbeddingProvider()

        with self.assertRaises(HTTPException) as context:
            await search(
                q="   ",
                session=session,
                embedding_provider=provider,
                limit=10,
            )

        self.assertEqual(context.exception.status_code, 422)
        self.assertEqual(session.execute_count, 0)
        self.assertIsNone(provider.texts)

    async def test_embedding_failure_returns_502(self) -> None:
        session = FakeSession([[]])
        provider = FakeEmbeddingProvider(EmbeddingProviderError("failed"))

        with self.assertRaises(HTTPException) as context:
            await search(
                q="address proof",
                session=session,
                embedding_provider=provider,
                limit=10,
            )

        self.assertEqual(context.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
