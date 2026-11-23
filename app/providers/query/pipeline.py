from collections.abc import Sequence

from app.core.config import Settings
from app.models import KnowledgeBase
from app.providers.llm.base import LLMProvider
from app.providers.query.base import QueryOptions, QueryProcessResult
from app.providers.query.llm_rewrite import LLMRewriteProcessor
from app.providers.query.noop import NoopQueryProcessor
from app.providers.query.synonym_expander import SynonymExpander


class QueryPipeline:
    def __init__(self, settings: Settings, llm: LLMProvider | None):
        self.default_enabled = settings.query_rewrite_enabled
        self.rewrite = LLMRewriteProcessor(llm, settings.query_rewrite_timeout_ms)
        self.noop = NoopQueryProcessor()
        self.synonyms = SynonymExpander()

    async def process(
        self, query: str, kb: KnowledgeBase | Sequence[KnowledgeBase], options: QueryOptions | None,
    ) -> QueryProcessResult:
        bases = [kb] if isinstance(kb, KnowledgeBase) else kb
        enabled = options.enabled if options and options.enabled is not None else self.default_enabled
        processor = self.rewrite if enabled else self.noop
        result = QueryProcessResult(raw_query=query, effective_query=query, search_query=query)
        result = await processor.process(result, bases)
        if options and options.synonym_enabled is False:
            return result
        return await self.synonyms.process(result, bases)
