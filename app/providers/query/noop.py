from collections.abc import Sequence

from app.models import KnowledgeBase
from app.providers.query.base import QueryProcessor, QueryProcessResult


class NoopQueryProcessor(QueryProcessor):
    async def process(self, result: QueryProcessResult, bases: Sequence[KnowledgeBase]) -> QueryProcessResult:
        return result
