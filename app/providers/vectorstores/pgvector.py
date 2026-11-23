from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.providers.vectorstores.base import ChunkMatch, ChunkToIndex, VectorStore
from app.repositories import chunks as chunk_repository


class PgVectorStore(VectorStore):
    name = "pgvector"

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def for_session(self, session: AsyncSession) -> "PgVectorStore":
        return type(self)(session)

    async def add_chunks(self, chunks: list[ChunkToIndex]) -> None:
        await chunk_repository.add_chunks(self.session, chunks)

    async def similarity_search(
        self, query_vector: list[float], *, tenant_id: str, kb_id: UUID, top_k: int
    ) -> list[ChunkMatch]:
        return await chunk_repository.search_chunks(
            self.session, query_vector, tenant_id=tenant_id, kb_id=kb_id, top_k=top_k
        )

    async def delete_by_document_id(self, document_id: UUID) -> None:
        await chunk_repository.delete_by_document_id(self.session, document_id)
