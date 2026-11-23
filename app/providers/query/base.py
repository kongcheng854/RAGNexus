from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from app.models import KnowledgeBase


class QueryOptions(BaseModel):
    synonym_enabled: bool | None = None
    enabled: bool | None = None
    strategy: Literal["rewrite"] = "rewrite"


class QueryProcessResult(BaseModel):
    raw_query: str
    effective_query: str
    search_query: str
    strategy: Literal["noop", "rewrite"] = "noop"
    rewrite_latency_ms: int = 0
    degraded: bool = False
    degraded_reason: str | None = None
    synonym_applied: bool = False
    synonym_expansions: list[str] = Field(default_factory=list)


class QueryProcessor(ABC):
    @abstractmethod
    async def process(self, result: QueryProcessResult, bases: Sequence[KnowledgeBase]) -> QueryProcessResult:
        """Apply one stage to this request's query."""
