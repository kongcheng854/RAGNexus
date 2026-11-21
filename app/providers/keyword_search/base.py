from abc import ABC, abstractmethod
from uuid import UUID

from app.core.error_codes import ErrorCode
from app.core.exceptions import ServiceError
from app.schemas.hybrid_search import KeywordChunk, KeywordMatch


class KeywordSearchError(ServiceError):
    def __init__(self, detail: str) -> None:
        super().__init__(ErrorCode.KEYWORD_SEARCH_ERROR, detail=detail)


class KeywordSearchProvider(ABC):
    name: str

    @abstractmethod
    async def add_chunks(self, chunks: list[KeywordChunk]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def keyword_search(
        self, *, query: str, tenant_id: str, kb_id: UUID, top_k: int = 20
    ) -> list[KeywordMatch]:
        raise NotImplementedError

    @abstractmethod
    async def delete_by_document_id(self, document_id: UUID) -> None:
        raise NotImplementedError
