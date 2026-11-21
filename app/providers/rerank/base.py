from abc import ABC, abstractmethod

from app.schemas.rerank import RerankCandidate, RerankedChunk


class RerankProvider(ABC):
    name: str

    @abstractmethod
    async def rerank(
        self, *, query: str, chunks: list[RerankCandidate], top_n: int
    ) -> list[RerankedChunk]:
        raise NotImplementedError
