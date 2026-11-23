import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.observability.langfuse_client import langfuse_connection
from app.services.rate_limit_service import RateLimitService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.index_dispatch import recover_dispatches
from app.api.errors import register_error_handlers
from app.api.request_logging import log_api_request
from app.api.v1.router import router
from app.core.config import Settings
from app.schemas.document import DocumentUpload
from app.core.logging import AppLogging, log_event
from app.providers.index_clients import index_clients, model_client
from app.providers.llm.base import LLMProvider
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider
from app.providers.rerank.base import RerankProvider
from app.providers.rerank.llm import LLMRerankProvider
from app.providers.rerank.noop import NoopRerankProvider


logger = logging.getLogger(__name__)


async def create_llm(settings: Settings, clients: AsyncExitStack) -> LLMProvider | None:

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
    return llm


def create_reranker(settings: Settings, llm: LLMProvider | None) -> RerankProvider:
    if settings.rerank_provider == "noop":
        return NoopRerankProvider()
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
                    app.state.langfuse = clients.enter_context(langfuse_connection(settings))
                    redis = await clients.enter_async_context(Redis.from_url(settings.celery_broker_url, socket_timeout=2, socket_connect_timeout=2))
                    app.state.rate_limits = RateLimitService(redis)
                    app.state.embedding_provider, app.state.keyword_provider = await clients.enter_async_context(
                        index_clients(settings)
                    )
                    app.state.llm = await create_llm(settings, clients)
                    app.state.rerank_provider = create_reranker(settings, app.state.llm)
                    log_event(logger, logging.INFO, "SERVICE_STARTED", environment=settings.app_env)
                    recovery = asyncio.create_task(recover_dispatches(app.state.session_factory))
                    try:
                        yield
                    finally:
                        recovery.cancel()
                        await asyncio.gather(recovery, return_exceptions=True)
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
    app.state.langfuse = None
    app.state.settings = settings
    register_error_handlers(app)
    app.add_middleware(log_api_request(log_payloads=settings.log_payloads, limit=settings.log_payload_limit))
    app.include_router(router)
    # The dual-media upload route dispatches on Content-Type before validating the JSON body.
    default_openapi = app.openapi

    def openapi():
        schema = default_openapi()
        schema["components"]["schemas"]["DocumentUpload"] = DocumentUpload.model_json_schema()
        return schema

    app.openapi = openapi
    return app


app = create_app()
