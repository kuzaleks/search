import asyncio
import unittest
from types import SimpleNamespace

from app.config import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL
from app.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    EmbeddingTimeoutError,
    OpenAIEmbeddingProvider,
)


class FakeEmbeddings:
    def __init__(self, data: list[SimpleNamespace]) -> None:
        self.data = data
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(data=self.data)


class FakeOpenAIClient:
    def __init__(self, data: list[SimpleNamespace]) -> None:
        self.embeddings = FakeEmbeddings(data)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class SlowEmbeddings:
    async def create(self, **kwargs):
        await asyncio.sleep(1)


class SlowOpenAIClient(FakeOpenAIClient):
    def __init__(self) -> None:
        super().__init__([])
        self.embeddings = SlowEmbeddings()


class OpenAIEmbeddingProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_embeds_all_chunks_in_one_ordered_request(self) -> None:
        first = [0.1] * EMBEDDING_DIMENSIONS
        second = [0.2] * EMBEDDING_DIMENSIONS
        client = FakeOpenAIClient(
            [
                SimpleNamespace(index=1, embedding=second),
                SimpleNamespace(index=0, embedding=first),
            ]
        )
        provider = OpenAIEmbeddingProvider(
            api_key=None,
            timeout=30,
            client=client,
        )

        result = await provider.embed(["first chunk", "second chunk"])

        self.assertEqual(result, [first, second])
        self.assertEqual(len(client.embeddings.calls), 1)
        self.assertEqual(
            client.embeddings.calls[0],
            {
                "input": ["first chunk", "second chunk"],
                "model": EMBEDDING_MODEL,
                "dimensions": EMBEDDING_DIMENSIONS,
                "encoding_format": "float",
            },
        )

    async def test_missing_api_key_is_a_configuration_error(self) -> None:
        provider = OpenAIEmbeddingProvider(api_key=None, timeout=30)

        with self.assertRaises(EmbeddingConfigurationError):
            await provider.embed(["chunk"])

    async def test_rejects_vectors_with_wrong_dimensions(self) -> None:
        client = FakeOpenAIClient(
            [SimpleNamespace(index=0, embedding=[0.1, 0.2])]
        )
        provider = OpenAIEmbeddingProvider(
            api_key=None,
            timeout=30,
            client=client,
        )

        with self.assertRaises(EmbeddingProviderError):
            await provider.embed(["chunk"])

    async def test_enforces_overall_embedding_timeout(self) -> None:
        provider = OpenAIEmbeddingProvider(
            api_key=None,
            timeout=0.001,
            max_retries=1,
            client=SlowOpenAIClient(),
        )

        with self.assertRaisesRegex(
            EmbeddingTimeoutError,
            "timed out after 0.001 seconds",
        ):
            await provider.embed(["chunk"])


if __name__ == "__main__":
    unittest.main()
