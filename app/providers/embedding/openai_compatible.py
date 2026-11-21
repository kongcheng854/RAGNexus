from math import isfinite

import httpx
from pydantic import BaseModel, ConfigDict

from app.core.error_codes import ErrorCode
from app.core.exceptions import EmbeddingError
from app.core.model_logging import log_model_failure, log_model_request, log_model_response
from app.providers.embedding.base import EmbeddingProvider
from app.providers.openai_errors import upstream_error_code


class EmbeddingItem(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    index: int
    embedding: list[float]


class EmbeddingResponse(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    data: list[EmbeddingItem]


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self, client: httpx.AsyncClient, model: str, *,
        log_payloads: bool = False, log_payload_limit: int = 2048,
    ) -> None:
        self.client = client
        self.model = model
        self.log_payloads = log_payloads
        self.log_payload_limit = log_payload_limit

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 32):
            batch = await self._embed_batch(
                texts[start:start + 32], expected_dimensions=len(vectors[0]) if vectors else None
            )
            vectors.extend(batch)
        return vectors

    async def _embed_batch(
        self, texts: list[str], *, expected_dimensions: int | None
    ) -> list[list[float]]:
        started = log_model_request(
            self.model, texts, operation="embedding", log_payloads=self.log_payloads,
            limit=self.log_payload_limit,
        )
        upstream_status = None
        try:
            response = await self.client.post(
                "embeddings", json={"model": self.model, "input": texts}
            )
            upstream_status = response.status_code
            response.raise_for_status()
            vectors = self._decode_vectors(response, len(texts), expected_dimensions)
        except (httpx.HTTPError, EmbeddingError) as exc:
            if isinstance(exc, httpx.TimeoutException):
                error = ErrorCode.LLM_TIMEOUT
            elif isinstance(exc, httpx.HTTPStatusError):
                error = upstream_error_code(exc.response)
            elif isinstance(exc, EmbeddingError):
                error = exc.error
            else:
                error = ErrorCode.LLM_ERROR

            log_model_failure(
                self.model, exc, operation="embedding", started=started,
                error=error, upstream_status=upstream_status,
            )
            if isinstance(exc, EmbeddingError):
                raise
            raise EmbeddingError(error=error) from exc

        log_model_response(
            self.model, vectors, operation="embedding", started=started,
            log_payloads=self.log_payloads, limit=self.log_payload_limit,
            vector_count=len(vectors), dimensions=len(vectors[0]), upstream_status=upstream_status,
        )
        return vectors

    @staticmethod
    def _decode_vectors(
        response: httpx.Response, count: int, expected_dimensions: int | None
    ) -> list[list[float]]:
        try:
            items = EmbeddingResponse.model_validate(response.json()).data
        except ValueError as exc:
            raise EmbeddingError(
                "invalid embedding response", error=ErrorCode.LLM_NO_RESPONSE
            ) from exc

        items.sort(key=lambda item: item.index)
        if [item.index for item in items] != list(range(count)):
            raise EmbeddingError("invalid embedding indices", error=ErrorCode.LLM_NO_RESPONSE)
        vectors = [item.embedding for item in items]
        dimensions = expected_dimensions if expected_dimensions is not None else len(vectors[0])
        for vector in vectors:
            if len(vector) != dimensions or not any(vector) or not all(isfinite(v) for v in vector):
                raise EmbeddingError("invalid embedding vector", error=ErrorCode.LLM_NO_RESPONSE)
        return vectors
