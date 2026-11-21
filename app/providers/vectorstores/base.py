from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ChunkToIndex:
    id: UUID
    tenant_id: str
    kb_id: UUID
    document_id: UUID
    title: str
    content: str
    chunk_metadata: dict[str, int]
    embedding: list[float]


@dataclass(frozen=True)
class ChunkMatch:
    document_id: UUID
    chunk_id: UUID
    title: str
    content: str
    score: float


class VectorStore(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def add_chunks(self, chunks: list[ChunkToIndex]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def similarity_search(
        self, query_vector: list[float], *, tenant_id: str, kb_id: UUID, top_k: int
    ) -> list[ChunkMatch]:
        raise NotImplementedError

    @abstractmethod
    async def delete_by_document_id(self, document_id: UUID) -> None:
        raise NotImplementedError
