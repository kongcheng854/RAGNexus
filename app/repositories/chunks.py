from dataclasses import asdict
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Document, IndexStatus
from app.providers.vectorstores.base import ChunkMatch, ChunkToIndex
from app.schemas.hybrid_search import KeywordMatch
from app.schemas.rerank import RerankCandidate


async def add_chunks(session: AsyncSession, chunks: list[ChunkToIndex]) -> None:
    session.add_all(Chunk(**asdict(chunk)) for chunk in chunks)
    await session.flush()


async def search_chunks(
    session: AsyncSession,
    query_vector: list[float],
    *,
    tenant_id: str,
    kb_id: UUID,
    top_k: int,
) -> list[ChunkMatch]:
    distance = Chunk.embedding.cosine_distance(query_vector)
    statement = (
        select(
            Chunk.document_id, Chunk.id.label("chunk_id"), Chunk.title,
            Chunk.content, (1.0 - distance).label("score"),
        )
        .where(
            Chunk.tenant_id == tenant_id,
            Chunk.kb_id == kb_id,
            func.vector_dims(Chunk.embedding) == len(query_vector),
        )
        .order_by(distance, Chunk.id)
        .limit(top_k)
    )
    rows = await session.execute(statement)
    return [ChunkMatch(**row) for row in rows.mappings()]


async def delete_by_document_id(session: AsyncSession, document_id: UUID) -> None:
    await session.execute(delete(Chunk).where(Chunk.document_id == document_id))


async def resolve_keyword_matches(
    session: AsyncSession, matches: list[KeywordMatch], tenant_id: str, kb_id: UUID
) -> list[RerankCandidate]:
    if not matches:
        return []
    records = await session.scalars(
        select(Chunk).join(Document, Chunk.document_id == Document.id).where(
            Chunk.id.in_([match.chunk_id for match in matches]),
            Chunk.tenant_id == tenant_id, Chunk.kb_id == kb_id,
            Document.status == IndexStatus.SUCCESS,
        )
    )
    by_id = {chunk.id: chunk for chunk in records}
    found = []
    seen = set()
    for match in matches:
        chunk = by_id.get(match.chunk_id)
        if chunk is not None and chunk.id not in seen:
            seen.add(chunk.id)
            found.append(RerankCandidate(
                document_id=chunk.document_id, chunk_id=chunk.id, title=chunk.title,
                content=chunk.content, score=match.bm25_score,
            ))
    return found
