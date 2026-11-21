import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import register_error_handlers
from app.api.request_logging import log_api_request
from app.api.v1.router import router
from app.core.config import Settings
from app.core.logging import AppLogging, log_event
from app.providers.embedding.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.providers.keyword_search.elasticsearch import ElasticsearchKeywordSearchProvider
from app.providers.llm.base import LLMProvider
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider
from app.providers.rerank.base import RerankProvider
from app.providers.rerank.llm import LLMRerankProvider
from app.providers.rerank.noop import NoopRerankProvider


logger = logging.getLogger(__name__)


def model_client(base_url: str, api_key: str, timeout: float) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout)


async def create_reranker(settings: Settings, clients: AsyncExitStack) -> RerankProvider:
    if settings.rerank_provider == "noop":
        return NoopRerankProvider()

    llm: LLMProvider | None = None
    if settings.llm_base_url is not None and settings.llm_model is not None:
        client = await clients.enter_async_context(
            model_client(
                str(settings.llm_base_url), settings.llm_api_key.get_secret_value(),
                settings.llm_timeout_seconds,
            )
        )
        providers = {"openai_compatible": OpenAICompatibleLLMProvider}
        llm = providers[settings.llm_provider](
            client, settings.llm_model, timeout_seconds=settings.llm_timeout_seconds,
            log_payloads=settings.log_payloads, log_payload_limit=settings.log_payload_limit,
        )
    return LLMRerankProvider(
        llm, chunk_max_chars=settings.rerank_chunk_max_chars,
        temperature=settings.rerank_temperature,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logs = AppLogging(
            directory=settings.log_dir, level=settings.log_level, max_bytes=settings.log_max_bytes,
            backups=settings.log_backup_count, queue_size=settings.log_queue_size, color=settings.log_color,
        )
        logs.start()
        try:
            engine = create_async_engine(
                settings.database_url, pool_pre_ping=True, hide_parameters=True
            )
            try:
                app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
                async with AsyncExitStack() as clients:
                    client = await clients.enter_async_context(
                        model_client(
                            str(settings.model_base_url), settings.model_api_key.get_secret_value(), 30.0
                        )
                    )
                    app.state.embedding_provider = OpenAICompatibleEmbeddingProvider(
                        client, settings.embedding_model, log_payloads=settings.log_payloads,
                        log_payload_limit=settings.log_payload_limit,
                    )
                    app.state.rerank_provider = await create_reranker(settings, clients)
                    keyword_client = await clients.enter_async_context(httpx.AsyncClient(
                        base_url=str(settings.elasticsearch_url),
                        timeout=settings.elasticsearch_timeout_seconds,
                    ))
                    app.state.keyword_provider = ElasticsearchKeywordSearchProvider(
                        keyword_client, settings.elasticsearch_index
                    )
                    log_event(logger, logging.INFO, "SERVICE_STARTED", environment=settings.app_env)
                    yield
            finally:
                await engine.dispose()
        except Exception:
            logger.exception("SERVICE_LIFESPAN_FAILED")
            raise
        finally:
            log_event(logger, logging.INFO, "SERVICE_STOPPED")
            await logs.stop()

    app = FastAPI(
        title="RAGNexus",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    app.state.settings = settings
    register_error_handlers(app)
    app.add_middleware(log_api_request(log_payloads=settings.log_payloads, limit=settings.log_payload_limit))
    app.include_router(router)
    return app


app = create_app()
