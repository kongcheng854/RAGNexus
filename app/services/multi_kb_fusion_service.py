from collections.abc import Iterable, Sequence
from uuid import UUID

from app.schemas.rerank import RerankCandidate


def fuse_by_rrf(rankings: Iterable[Sequence[RerankCandidate]], rrf_k: int) -> list[RerankCandidate]:
    """Fuse ordered KB lists. Equal scores retain request order, including ties at the cutoff."""
    merged: dict[UUID, RerankCandidate] = {}
    for ranking in rankings:
        seen: set[UUID] = set()
        for rank, chunk in enumerate(ranking, start=1):
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            contribution = 1.0 / (rrf_k + rank)
            if chunk.chunk_id in merged:
                merged[chunk.chunk_id].score += contribution
            else:
                merged[chunk.chunk_id] = chunk.model_copy(update={"score": contribution})
    return sorted(merged.values(), key=lambda chunk: chunk.score, reverse=True)
