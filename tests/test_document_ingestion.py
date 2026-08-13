import unittest
from uuid import uuid4

from fastapi import HTTPException

from app.api import create_document
from app.config import EMBEDDING_DIMENSIONS
from app.embeddings import EmbeddingProviderError
from app.models import Client, Document
from app.schemas import DocumentCreate


class FakeSession:
    def __init__(self, client: Client | None) -> None:
        self.client = client
        self.added: Document | None = None
        self.rolled_back = False
        self.committed = False

    async def get(self, model, identifier):
        return self.client

    async def rollback(self) -> None:
        self.rolled_back = True

    def add(self, document: Document) -> None:
        self.added = document

    async def commit(self) -> None:
        self.committed = True


class FakeEmbeddingProvider:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.texts: list[str] | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts = texts
        if self.error:
            raise self.error
        return [[0.1] * EMBEDDING_DIMENSIONS for _ in texts]


class DocumentIngestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_ingests_document_and_all_chunks(self) -> None:
        client_id = uuid4()
        session = FakeSession(Client(id=client_id))
        provider = FakeEmbeddingProvider()
        content = " ".join(f"word-{index}" for index in range(500))

        document = await create_document(
            client_id=client_id,
            document_data=DocumentCreate(title="Statement", content=content),
            session=session,
            embedding_provider=provider,
        )

        self.assertTrue(session.rolled_back)
        self.assertTrue(session.committed)
        self.assertIs(session.added, document)
        self.assertEqual(len(document.chunks), 2)
        self.assertEqual(
            provider.texts,
            [
                content[chunk.start_offset : chunk.end_offset]
                for chunk in document.chunks
            ],
        )
        self.assertEqual(document.chunks[0].start_offset, 0)
        self.assertEqual(document.chunks[1].start_offset, 3040)

    async def test_unknown_client_returns_404_before_embedding(self) -> None:
        session = FakeSession(client=None)
        provider = FakeEmbeddingProvider()

        with self.assertRaises(HTTPException) as context:
            await create_document(
                client_id=uuid4(),
                document_data=DocumentCreate(title="Title", content="Content"),
                session=session,
                embedding_provider=provider,
            )

        self.assertEqual(context.exception.status_code, 404)
        self.assertIsNone(provider.texts)
        self.assertFalse(session.committed)

    async def test_embedding_failure_does_not_write_document(self) -> None:
        client_id = uuid4()
        session = FakeSession(Client(id=client_id))
        provider = FakeEmbeddingProvider(EmbeddingProviderError("failed"))

        with self.assertRaises(HTTPException) as context:
            await create_document(
                client_id=client_id,
                document_data=DocumentCreate(title="Title", content="Content"),
                session=session,
                embedding_provider=provider,
            )

        self.assertEqual(context.exception.status_code, 502)
        self.assertIsNone(session.added)
        self.assertFalse(session.committed)


if __name__ == "__main__":
    unittest.main()
