import asyncio
import unittest
from contextlib import nullcontext
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import httpx

from app.api.dependencies import (
    get_embedding_provider, get_keyword_provider, get_llm, get_rerank_provider, get_vector_store,
)
from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import EmbeddingError
from app.db.session import get_session
from app.main import create_app
from app.models import KnowledgeBase
from app.observability.langfuse_client import RetrieveObservability
from app.providers.keyword_search.base import KeywordSearchError
from app.providers.rerank.noop import NoopRerankProvider
from app.providers.vectorstores.base import ChunkMatch
from app.schemas.hybrid_search import KeywordMatch
from app.schemas.rerank import RerankCandidate
from tests.rate_support import isolated_rates


class RetrieveRobustnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.a, self.b = uuid4(), uuid4()
        self.session = Mock(bind=None, begin=nullcontext, scalar=AsyncMock(return_value=False))
        self.embedding = Mock(embed=AsyncMock(return_value=[[1.0]]))
        self.vector = Mock(similarity_search=AsyncMock(return_value=[
            ChunkMatch(uuid4(), uuid4(), "向量", "退款规则", 0.8),
        ]))
        self.vector.name = "pgvector"
        self.vector.for_session.return_value = self.vector
        self.keyword = Mock(keyword_search=AsyncMock(return_value=[KeywordMatch(chunk_id=uuid4(), bm25_score=9)]))
        self.keyword.name = "elasticsearch"
        def resolve(_session, matches, _tenant, _kb):
            return [RerankCandidate(chunk_id=m.chunk_id, document_id=uuid4(), title="条款", content="原文",
                                    score=m.bm25_score) for m in matches]
        self.resolve = self.enterContext(patch("app.services.hybrid_search_service.resolve_keyword_matches",
                                               new_callable=AsyncMock, side_effect=resolve))
        bases = {kb: KnowledgeBase(id=kb, tenant_id="tenant_demo", name=str(kb), settings={}) for kb in (self.a, self.b)}
        self.scope = self.enterContext(patch("app.repositories.catalog.get_knowledge_bases",
                                             new_callable=AsyncMock, return_value=bases))
        self.log = self.enterContext(patch("app.repositories.catalog.add_retrieval_log", new_callable=AsyncMock))
        self.trace = self.enterContext(patch("app.services.rag_service.RetrieveObservability", wraps=RetrieveObservability))
        self.settings = Settings(_env_file=None, auth_enabled=False)
        app = create_app(self.settings)
        self.rates = isolated_rates(app, self)
        app.dependency_overrides.update({
            get_session: lambda: self.session, get_embedding_provider: lambda: self.embedding,
            get_vector_store: lambda: self.vector, get_keyword_provider: lambda: self.keyword,
            get_llm: lambda: None, get_rerank_provider: NoopRerankProvider,
        })
        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1/",
        ))

    async def search(self, **changes):
        return await self.client.post("rag/retrieve", json={
            "user_id": "tester", "kb_id": str(self.a), "query": "退款", "profile": "balanced", **changes,
        })

    async def test_vector_store_failure_preserves_bm25_scores_and_records_diagnostics(self):
        self.vector.similarity_search.side_effect = TimeoutError()
        result = (await self.search()).raise_for_status().json()["data"]
        details = result["metadata"]["retrieval"]
        self.assertTrue(details["degraded"])
        self.assertEqual(details["degraded_reason"], "vector search failed")
        self.assertNotIn("failed_kb_ids", details)
        hit, = result["retrieved_chunks"]
        self.assertEqual((hit["retrieval_source"], hit["score"], hit["bm25_rank"]), ("bm25", 9, 1))
        self.assertIsNone(hit["vector_score"])
        self.assertEqual(self.log.await_args.args[1].retrieval_metadata, details)
        self.assertEqual(self.trace.call_args.kwargs["metadata"]["retrieval"], details)

    async def test_es_failure_or_rehydration_failure_preserves_vector_scores(self):
        for source in (self.keyword.keyword_search, self.resolve):
            with self.subTest(source=source):
                source.side_effect = KeywordSearchError("backend unavailable")
                result = (await self.search()).raise_for_status().json()["data"]
                details = result["metadata"]["retrieval"]
                self.assertTrue(details["degraded"])
                self.assertEqual(details["degraded_reason"], "bm25 search failed")
                hit, = result["retrieved_chunks"]
                self.assertEqual((hit["retrieval_source"], hit["score"], hit["vector_rank"]), ("vector", 0.8, 1))
                self.assertIsNone(hit["bm25_score"])
                source.side_effect = None

    async def test_empty_successful_channel_is_distinct_from_failure(self):
        self.keyword.keyword_search.return_value = []
        data = (await self.search()).raise_for_status().json()["data"]
        self.assertFalse(data["metadata"]["retrieval"]["degraded"])
        self.assertAlmostEqual(data["retrieved_chunks"][0]["score"], 1 / 61)
        self.embedding.embed.side_effect = EmbeddingError()
        self.session.scalar.return_value = True
        data = (await self.search()).raise_for_status().json()["data"]
        self.assertEqual(data["retrieved_chunks"], [])
        details = data["metadata"]["retrieval"]
        self.assertEqual((details["empty_reason"], details["degraded_reason"]), ("no_chunks_matched", "vector search failed"))

    async def test_shared_embedding_failure_runs_each_bm25_and_charges_once(self):
        self.embedding.embed.side_effect = EmbeddingError()
        data = (await self.search(kb_ids=[str(self.a), str(self.b)])).raise_for_status().json()["data"]
        self.embedding.embed.assert_awaited_once()
        self.vector.similarity_search.assert_not_awaited()
        self.assertEqual(self.keyword.keyword_search.await_count, 2)
        details = data["metadata"]["retrieval"]
        self.assertFalse(details["partial_kb_success"])
        self.assertTrue(details["degraded"])
        self.assertEqual(details["degraded_reason"], "vector search failed")
        self.assertTrue(all(item["status"] == "degraded" for item in details["per_kb_metadata"].values()))
        self.assertTrue(all(hit["retrieval_source"] == "bm25" for hit in data["retrieved_chunks"]))
        self.assertEqual(await self.rates.daily_usage("tenant_demo"), 1)

    async def test_both_channels_down_is_a_typed_failure(self):
        self.embedding.embed.side_effect = EmbeddingError()
        self.keyword.keyword_search.side_effect = KeywordSearchError("offline")
        response = await self.search()
        self.assertEqual((response.status_code, response.json()["code"]), (503, ErrorCode.RETRIEVAL_FAILED))
        self.log.assert_not_awaited()
        self.assertEqual(await self.rates.daily_usage("tenant_demo"), 0)

    async def test_single_modes_keep_the_upstream_error(self):
        self.embedding.embed.side_effect = EmbeddingError()
        self.keyword.keyword_search.side_effect = KeywordSearchError("offline")
        for mode, code in (("vector", ErrorCode.LLM_ERROR), ("bm25", ErrorCode.KEYWORD_SEARCH_ERROR)):
            result = await self.search(profile="custom", retrieval_options={"mode": mode})
            self.assertEqual(result.json()["code"], code)
        self.assertEqual(self.keyword.keyword_search.await_count, 1)
        self.embedding.embed.assert_awaited_once()

    async def test_partial_and_total_kb_failure(self):
        def lexical(**kwargs):
            if kwargs["kb_id"] == self.a:
                raise KeywordSearchError("private endpoint")
            return [KeywordMatch(chunk_id=uuid4(), bm25_score=8)]
        self.keyword.keyword_search.side_effect = lexical
        body = {"profile": "custom", "retrieval_options": {"mode": "bm25"}, "kb_ids": [str(self.a), str(self.b)]}
        result = (await self.search(**body)).raise_for_status().json()["data"]
        details = result["metadata"]["retrieval"]
        self.assertEqual(details["failed_kb_ids"], [str(self.a)])
        self.assertTrue(details["partial_kb_success"] and details["degraded"])
        self.assertEqual(details["per_kb_metadata"][str(self.a)], {"status": "failed", "error": "KEYWORD_SEARCH_ERROR"})
        self.assertEqual(details["per_kb_metadata"][str(self.b)], {"status": "ok"})
        hit, = result["retrieved_chunks"]
        self.assertEqual(hit["kb_id"], str(self.b))
        self.assertAlmostEqual(hit["score"], 1 / 61)
        self.assertEqual(self.log.await_args.args[1].retrieval_metadata, details)
        self.keyword.keyword_search.side_effect = TimeoutError("private endpoint")
        failed = await self.search(**body)
        self.assertEqual((failed.status_code, failed.json()["code"]), (503, ErrorCode.RETRIEVAL_FAILED))
        self.assertEqual(failed.json()["data"]["failed_kb_ids"], body["kb_ids"])
        self.assertEqual(failed.json()["data"]["per_kb_metadata"][str(self.b)]["error"], "API_TIMEOUT")
        self.assertNotIn("private endpoint", failed.text)
        self.assertEqual(await self.rates.daily_usage("tenant_demo"), 1)

    async def test_shared_embedding_outage_with_one_failed_keyword_lookup(self):
        def lexical(**kwargs):
            if kwargs["kb_id"] == self.b:
                raise TimeoutError()
            return [KeywordMatch(chunk_id=uuid4(), bm25_score=7)]

        self.embedding.embed.side_effect = EmbeddingError()
        self.keyword.keyword_search.side_effect = lexical
        result = (await self.search(kb_ids=[str(self.a), str(self.b)])).raise_for_status().json()["data"]
        self.embedding.embed.assert_awaited_once()
        details = result["metadata"]["retrieval"]
        self.assertEqual(details["failed_kb_ids"], [str(self.b)])
        self.assertEqual(details["per_kb_metadata"][str(self.a)]["degraded_reason"], "vector search failed")
        self.assertEqual(details["per_kb_metadata"][str(self.b)]["error"], "RETRIEVAL_FAILED")
        hit, = result["retrieved_chunks"]
        self.assertEqual((hit["kb_id"], hit["bm25_score"], hit["retrieval_source"]), (str(self.a), 7, "bm25"))
        self.assertAlmostEqual(hit["score"], 1 / 61)

    async def test_cancellation_is_not_partial_success(self):
        self.keyword.keyword_search.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.search(kb_ids=[str(self.a), str(self.b)])
        self.log.assert_not_awaited()

    async def test_query_limits_precede_recall_and_allow_a_configured_increase(self):
        self.settings.query_max_length = 3
        for invalid in ("", "  \t\n", "四个汉字"):
            response = await self.search(query=invalid)
            self.assertEqual((response.status_code, response.json()["code"]), (422, ErrorCode.PARAM_ERROR))
        self.scope.assert_not_awaited()
        self.embedding.embed.assert_not_awaited()
        (await self.search(query="三个字")).raise_for_status()
        self.settings.query_max_length = 2001
        (await self.search(query="检" * 2001)).raise_for_status()
