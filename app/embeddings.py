from functools import lru_cache

from openai import APIError, AsyncOpenAI

from app.config import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    get_settings,
)


class EmbeddingProviderError(Exception):
    """Raised when embeddings cannot be generated or validated."""


class EmbeddingConfigurationError(EmbeddingProviderError):
    """Raised when the embedding provider is not configured."""


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        api_key: str | None,
        timeout: float,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._api_key = api_key.strip() if api_key else None
        self._timeout = timeout
        self._client = client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._client is None:
            if self._api_key is None:
                raise EmbeddingConfigurationError(
                    "OPENAI_API_KEY is not configured"
                )
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                timeout=self._timeout,
            )

        try:
            response = await self._client.embeddings.create(
                input=texts,
                model=EMBEDDING_MODEL,
                dimensions=EMBEDDING_DIMENSIONS,
                encoding_format="float",
            )
        except APIError as error:
            raise EmbeddingProviderError(
                "OpenAI embedding request failed"
            ) from error

        vectors = [
            item.embedding for item in sorted(response.data, key=lambda x: x.index)
        ]
        if len(vectors) != len(texts) or any(
            len(vector) != EMBEDDING_DIMENSIONS for vector in vectors
        ):
            raise EmbeddingProviderError(
                "Embedding provider returned an unexpected response"
            )

        return vectors

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()


@lru_cache
def get_embedding_provider() -> OpenAIEmbeddingProvider:
    settings = get_settings()
    api_key = (
        settings.openai_api_key.get_secret_value()
        if settings.openai_api_key
        else None
    )
    return OpenAIEmbeddingProvider(
        api_key=api_key,
        timeout=settings.embedding_timeout_seconds,
    )


async def close_embedding_provider() -> None:
    await get_embedding_provider().close()
