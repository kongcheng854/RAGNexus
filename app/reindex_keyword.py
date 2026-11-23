"""Backfill successful PostgreSQL chunks into the configured keyword index."""
import argparse
import asyncio
import sys
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.models import Chunk, Document, IndexStatus
from app.providers.keyword_search.elasticsearch import ElasticsearchKeywordSearchProvider
from app.schemas.hybrid_search import KeywordChunk


async def reindex(tenant_id: str | None, kb_id: UUID | None) -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    sessions = async_sessionmaker(engine)
    try:
        async with httpx.AsyncClient(
            base_url=str(settings.elasticsearch_url), timeout=settings.elasticsearch_timeout_seconds,
        ) as client:
            provider = ElasticsearchKeywordSearchProvider(client, settings.elasticsearch_index)
            cursor = None
            count = 0
            while True:
                statement = select(
                    Chunk.id.label("chunk_id"), Chunk.tenant_id, Chunk.kb_id, Chunk.document_id,
                    Chunk.title, Chunk.content, Chunk.chunk_metadata.label("metadata"), Chunk.created_at,
                ).join(Document, Chunk.document_id == Document.id).where(
                    Document.status == IndexStatus.SUCCESS
                ).order_by(Chunk.id).limit(128)
                if cursor is not None:
                    statement = statement.where(Chunk.id > cursor)
                if tenant_id is not None:
                    statement = statement.where(Chunk.tenant_id == tenant_id)
                if kb_id is not None:
                    statement = statement.where(Chunk.kb_id == kb_id)
                async with sessions() as session, session.begin():
                    # Serialize backfill writes with document deletion and reindex.
                    rows = (await session.execute(statement.with_for_update(read=True, of=Document))).mappings().all()
                    if not rows:
                        break
                    await provider.add_chunks([KeywordChunk.model_validate(dict(row)) for row in rows])
                cursor = rows[-1]["chunk_id"]
                count += len(rows)
                print(f"Indexed {count} chunks", flush=True)
            print(f"Keyword backfill complete: {count} chunks")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id")
    parser.add_argument("--kb-id", type=UUID)
    args = parser.parse_args()
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(reindex(args.tenant_id, args.kb_id))


if __name__ == "__main__":
    main()
