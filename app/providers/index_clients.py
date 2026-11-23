from contextlib import asynccontextmanager

import httpx

from app.core.config import Settings
from app.providers.embedding.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.providers.keyword_search.elasticsearch import ElasticsearchKeywordSearchProvider


def model_client(base_url: str, api_key: str, timeout: float) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout)


@asynccontextmanager
async def index_clients(settings: Settings):
    async with (
        model_client(str(settings.model_base_url), settings.model_api_key.get_secret_value(), 30) as client,
        httpx.AsyncClient(base_url=str(settings.elasticsearch_url), timeout=settings.elasticsearch_timeout_seconds) as es,
    ):
        yield (
            OpenAICompatibleEmbeddingProvider(
                client, settings.embedding_model, log_payloads=settings.log_payloads,
                log_payload_limit=settings.log_payload_limit,
            ),
            ElasticsearchKeywordSearchProvider(es, settings.elasticsearch_index),
        )
