from app.providers.rerank.base import RerankProvider
from app.schemas.rerank import RerankCandidate, RerankedChunk


class NoopRerankProvider(RerankProvider):
    name = "noop"

    async def rerank(
        self, *, query: str, chunks: list[RerankCandidate], top_n: int
    ) -> list[RerankedChunk]:
        return [RerankedChunk(**chunk.model_dump()) for chunk in chunks[:top_n]]
