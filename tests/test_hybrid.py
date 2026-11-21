import json
import unittest
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from pydantic import ValidationError

from app.providers.keyword_search.base import KeywordSearchError
from app.providers.keyword_search.elasticsearch import ElasticsearchKeywordSearchProvider, INDEX_MAPPING
from app.schemas.hybrid_search import KeywordChunk, RetrievalOptions
from app.schemas.rerank import RerankCandidate
from app.services.hybrid_search_service import reciprocal_rank_fusion


def candidate(score):
    return RerankCandidate(
        chunk_id=uuid4(), document_id=uuid4(), title="退款", content="七天内退款", score=score,
    )


class FusionTests(unittest.TestCase):
    def test_rrf_deduplicates_by_id_preserves_provenance_and_stable_ties(self):
        first, second, third = candidate(0.8), candidate(0.7), candidate(13.4)
        bm25_second = second.model_copy(update={"score": 99.0})
        result = reciprocal_rank_fusion(
            [first, second, first], [bm25_second, third], rrf_k=60, top_n=3,
        )
        self.assertEqual([hit.chunk_id for hit in result], [second.chunk_id, first.chunk_id, third.chunk_id])
        self.assertAlmostEqual(result[0].score, 1 / 62 + 1 / 61)
        self.assertEqual((result[0].vector_score, result[0].bm25_score), (0.7, 99.0))
        self.assertEqual((result[0].vector_rank, result[0].bm25_rank), (2, 1))
        self.assertEqual(result[0].retrieval_source, "hybrid")
        self.assertEqual(result[1].retrieval_source, "vector")
        self.assertEqual(result[2].retrieval_source, "bm25")
        self.assertEqual(reciprocal_rank_fusion([], [], rrf_k=60, top_n=20), [])
        self.assertEqual(
            reciprocal_rank_fusion([first], [third], rrf_k=60, top_n=1)[0].chunk_id, first.chunk_id,
        )

    def test_options_reject_invalid_limits_and_modes(self):
        for values in ({"mode": "bad"}, {"rrf_k": 0}, {"vector_top_k": True}, {"bm25_top_k": 1001}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                RetrievalOptions(**values)


class ElasticsearchProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_mapping_bulk_filtering_and_partial_write_failure(self):
        chunk = KeywordChunk(
            tenant_id="tenant", kb_id=uuid4(), document_id=uuid4(), chunk_id=uuid4(),
            title="标题", content="错误 ZX999", metadata={"position": 0}, created_at=datetime.now(timezone.utc),
        )
        calls = []
        ready = False
        bulk_fail = False

        def respond(request):
            nonlocal ready
            calls.append(request)
            if request.url.path.endswith("/_mapping"):
                return httpx.Response(200, json={"test_chunks": {"mappings": INDEX_MAPPING}}) if ready else httpx.Response(404)
            if request.method == "PUT":
                self.assertEqual(json.loads(request.content)["mappings"]["properties"]["title"]["analyzer"], "ik_max_word")
                ready = True
                return httpx.Response(200, json={"acknowledged": True})
            if request.url.path.endswith("/_bulk"):
                lines = [json.loads(line) for line in request.content.decode().splitlines()]
                self.assertEqual(lines[0]["index"]["_id"], str(chunk.chunk_id))
                self.assertEqual(lines[1]["chunk_id"], str(chunk.chunk_id))
                self.assertNotIn("embedding", lines[1])
                self.assertEqual(request.url.params["refresh"], "wait_for")
                return httpx.Response(200, json={
                    "errors": bulk_fail, "items": [{"index": {"status": 400 if bulk_fail else 201}}],
                })
            if request.url.path.endswith("/_delete_by_query"):
                self.assertEqual(json.loads(request.content)["query"]["term"]["document_id"], str(chunk.document_id))
                return httpx.Response(200, json={"deleted": 1, "failures": []})
            query = json.loads(request.content)
            self.assertEqual(query["query"]["bool"]["filter"], [
                {"term": {"tenant_id": "tenant"}}, {"term": {"kb_id": str(chunk.kb_id)}},
            ])
            self.assertEqual(query["query"]["bool"]["must"]["multi_match"]["fields"], ["title^2", "content"])
            return httpx.Response(200, json={"hits": {"hits": [{
                "_source": {"chunk_id": str(chunk.chunk_id), "tenant_id": "tenant", "kb_id": str(chunk.kb_id)},
                "_score": 12.4,
            }]}})

        async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(respond)) as client:
            provider = ElasticsearchKeywordSearchProvider(client, "test_chunks")
            await provider.add_chunks([chunk])
            found = await provider.keyword_search(query="ZX999", tenant_id="tenant", kb_id=chunk.kb_id)
            self.assertEqual(found[0].bm25_score, 12.4)
            await provider.delete_by_document_id(chunk.document_id)
            bulk_fail = True
            with self.assertRaises(KeywordSearchError):
                await provider.add_chunks([chunk])

    async def test_rejects_incompatible_analyzers(self):
        mapping = {"test_chunks": {"mappings": {"properties": {
            **INDEX_MAPPING["properties"], "title": {"type": "text", "analyzer": "standard"},
        }}}}
        async with httpx.AsyncClient(
            base_url="http://test", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=mapping)),
        ) as client:
            with self.assertRaises(KeywordSearchError):
                await ElasticsearchKeywordSearchProvider(client, "test_chunks").ensure_index()
