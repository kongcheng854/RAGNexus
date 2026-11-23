import asyncio
import logging
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from fastapi import Request
from langfuse.api import CreateSpanBody, IngestionEvent_SpanCreate, IngestionEvent_TraceCreate, TraceBody
from langfuse.api.client import FernLangfuse

from app.core.config import Settings


logger = logging.getLogger(__name__)


@contextmanager
def langfuse_connection(settings: Settings):
    if not settings.langfuse_enabled:
        yield None
        return
    secret = settings.langfuse_secret_key.get_secret_value()
    if not settings.langfuse_public_key or not secret:
        logger.warning("Langfuse project keys are missing")
        yield None
        return
    with ExitStack() as resources:
        try:
            http = resources.enter_context(httpx.Client(trust_env=False, timeout=3))
            client = FernLangfuse(
                base_url=str(settings.langfuse_host).rstrip("/"), username=settings.langfuse_public_key,
                password=secret, httpx_client=http, timeout=3,
            )
        except Exception as exc:
            logger.warning("Langfuse client initialization failed (%s)", type(exc).__name__)
            client = None
        yield client


def get_langfuse_client(request: Request) -> FernLangfuse | None:
    return request.app.state.langfuse


get_langfuse = get_langfuse_client


class RetrieveObservability:
    """Collect stage timings locally and flush one acknowledged v2 ingestion batch."""

    def __init__(self, client: FernLangfuse | None, *, query: str, metadata: dict):
        self.client = client
        self.trace_id = str(uuid4()) if client else None
        self.query = query
        self.metadata = metadata
        self.started = datetime.now(timezone.utc)
        self.spans: list[dict] = []

    @contextmanager
    def span(self, name: str):
        summary = {}
        if self.client is None:
            yield summary
            return
        stage = dict(name=name, start_time=datetime.now(timezone.utc), output=summary)
        try:
            yield summary
        except Exception as exc:
            stage.update(level="ERROR", status_message=type(exc).__name__)
            raise
        else:
            if summary.get("degraded"):
                stage["level"] = "WARNING"
        finally:
            stage["end_time"] = datetime.now(timezone.utc)
            self.spans.append(stage)

    async def flush(self, chunks=(), *, error: str | None = None) -> str | None:
        if self.client is None:
            return None
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            metadata = self.metadata if error is None else {**self.metadata, "error_type": error}
            trace = TraceBody(
                id=self.trace_id, name="rag_retrieve", timestamp=self.started,
                user_id=metadata["user_id"], metadata=metadata, input=self.query,
                output=[{"chunk_id": str(chunk.chunk_id), "score": chunk.score} for chunk in chunks],
            )
            trace_event = IngestionEvent_TraceCreate(type="trace-create", id=str(uuid4()), timestamp=timestamp, body=trace)
            batch = [trace_event]
            # A later SQL failure can update this trace without creating the same spans twice.
            stages, self.spans = self.spans, []
            for stage in stages:
                body = CreateSpanBody(id=str(uuid4()), trace_id=self.trace_id, **stage)
                batch.append(IngestionEvent_SpanCreate(type="span-create", id=str(uuid4()), timestamp=timestamp, body=body))
            receipt = await asyncio.to_thread(self.client.ingestion.batch, batch=batch)
            accepted = {ack.id for ack in receipt.successes if 200 <= ack.status < 300}
            accepted.difference_update(error.id for error in receipt.errors)
            if trace_event.id not in accepted:
                logger.warning("Langfuse did not acknowledge retrieval trace %s", self.trace_id)
                return None
            missing_spans = [event.body.name for event in batch[1:] if event.id not in accepted]
            if missing_spans:
                logger.warning("Langfuse trace %s is missing spans: %s", self.trace_id, ", ".join(missing_spans))
            return self.trace_id
        except Exception as exc:
            logger.warning("Langfuse trace delivery failed (%s)", type(exc).__name__)
            return None
