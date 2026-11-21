import asyncio
import json
from uuid import UUID

import httpx

from app.providers.keyword_search.base import KeywordSearchError, KeywordSearchProvider
from app.schemas.hybrid_search import KeywordChunk, KeywordMatch


INDEX_MAPPING = {
    "dynamic": "strict",
    "properties": {
        "tenant_id": {"type": "keyword"},
        "kb_id": {"type": "keyword"},
        "document_id": {"type": "keyword"},
        "chunk_id": {"type": "keyword"},
        "title": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"},
        "content": {"type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart"},
        "metadata": {"type": "object", "enabled": False},
        "created_at": {"type": "date"},
    },
}


class ElasticsearchKeywordSearchProvider(KeywordSearchProvider):
    name = "elasticsearch"

    def __init__(self, client: httpx.AsyncClient, index: str) -> None:
        self.client = client
        self.index = index
        self._ready = False
        self._index_lock = asyncio.Lock()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = await self.client.request(method, path, **kwargs)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("response must be an object")
            return body
        except (httpx.HTTPError, ValueError) as exc:
            raise KeywordSearchError("elasticsearch request failed") from exc

    async def ensure_index(self) -> None:
        if self._ready:
            return
        async with self._index_lock:
            if self._ready:
                return
            try:
                response = await self.client.get(f"{self.index}/_mapping")
                if response.status_code == 404:
                    created = await self.client.put(
                        self.index,
                        json={"settings": {"number_of_shards": 1, "number_of_replicas": 0}, "mappings": INDEX_MAPPING},
                    )
                    if created.status_code != 400 or created.json().get("error", {}).get("type") != "resource_already_exists_exception":
                        created.raise_for_status()
                    response = await self.client.get(f"{self.index}/_mapping")
                response.raise_for_status()
                properties = response.json()[self.index]["mappings"]["properties"]
                for field, expected in INDEX_MAPPING["properties"].items():
                    for key, value in expected.items():
                        if properties[field].get(key) != value:
                            raise ValueError(f"incompatible index mapping for {field}.{key}")
            except (httpx.HTTPError, KeyError, TypeError, ValueError, AttributeError) as exc:
                raise KeywordSearchError("elasticsearch index mapping or IK plugin unavailable") from exc
            self._ready = True

    async def add_chunks(self, chunks: list[KeywordChunk]) -> None:
        if not chunks:
            return
        await self.ensure_index()
        for start in range(0, len(chunks), 128):
            batch = chunks[start:start + 128]
            lines = []
            for chunk in batch:
                lines.append(json.dumps({"index": {"_id": str(chunk.chunk_id)}}, allow_nan=False))
                lines.append(chunk.model_dump_json())
            result = await self._request(
                "POST", f"{self.index}/_bulk",
                params={"refresh": "wait_for"},
                content="\n".join(lines) + "\n",
                headers={"Content-Type": "application/x-ndjson"},
            )
            items = result.get("items", [])
            if result.get("errors") is not False or len(items) != len(batch):
                raise KeywordSearchError("elasticsearch bulk indexing failed")
            for item in items:
                operation = item.get("index", {})
                if operation.get("status") not in (200, 201) or operation.get("error"):
                    raise KeywordSearchError("elasticsearch bulk item failed")

    async def keyword_search(
        self, *, query: str, tenant_id: str, kb_id: UUID, top_k: int = 20
    ) -> list[KeywordMatch]:
        await self.ensure_index()
        result = await self._request(
            "POST", f"{self.index}/_search",
            json={
                "size": top_k,
                "track_total_hits": False,
                "_source": ["chunk_id", "tenant_id", "kb_id"],
                "sort": [{"_score": "desc"}, {"chunk_id": "asc"}],
                "query": {"bool": {
                    "filter": [{"term": {"tenant_id": tenant_id}}, {"term": {"kb_id": str(kb_id)}}],
                    "must": {"multi_match": {"query": query, "fields": ["title^2", "content"]}},
                }},
            },
        )
        try:
            if result.get("timed_out") or result.get("_shards", {}).get("failed", 0):
                raise ValueError("incomplete search results")
            matches = []
            for hit in result["hits"]["hits"]:
                source = hit["_source"]
                if source["tenant_id"] != tenant_id or source["kb_id"] != str(kb_id):
                    raise ValueError("unexpected search scope")
                matches.append(KeywordMatch(chunk_id=source["chunk_id"], bm25_score=hit["_score"]))
            return matches
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise KeywordSearchError("invalid elasticsearch search response") from exc

    async def delete_by_document_id(self, document_id: UUID) -> None:
        result = await self._request(
            "POST", f"{self.index}/_delete_by_query",
            params={"refresh": "true", "conflicts": "proceed"},
            json={"query": {"term": {"document_id": str(document_id)}}},
        )
        if result.get("failures") or result.get("timed_out") or result.get("version_conflicts", 0):
            raise KeywordSearchError("elasticsearch document cleanup incomplete")
