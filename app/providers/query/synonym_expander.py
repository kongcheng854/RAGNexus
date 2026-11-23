from collections.abc import Sequence

from app.models import KnowledgeBase
from app.providers.query.base import QueryProcessor, QueryProcessResult


def matching_expansions(query: str, groups: list) -> list[str]:
    folded = query.casefold()
    expansions: list[str] = []
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        terms, words = group.get("terms"), group.get("expand")
        if not isinstance(terms, list) or not isinstance(words, list):
            continue
        if not any(isinstance(t, str) and t.strip() and t.strip().casefold() in folded for t in terms):
            continue
        for word in words:
            if isinstance(word, str) and (word := word.strip()) and word.casefold() not in seen:
                expansions.append(word)
                seen.add(word.casefold())
    return expansions


class SynonymExpander(QueryProcessor):
    async def process(self, result: QueryProcessResult, bases: Sequence[KnowledgeBase]) -> QueryProcessResult:
        groups = []
        for kb in bases:
            synonyms = kb.settings.get("synonyms") if isinstance(kb.settings, dict) else None
            if isinstance(synonyms, list):
                groups.extend(synonyms)
        if groups:
            words = matching_expansions(result.effective_query, groups)
            result.synonym_expansions = words
            result.synonym_applied = bool(words)
            result.search_query = " ".join([result.effective_query, *words])
        return result
