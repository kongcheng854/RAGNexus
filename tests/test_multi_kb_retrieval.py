import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select, update

from app.api.dependencies import get_embedding_provider, get_keyword_provider, get_llm, get_rerank_provider
from app.core.auth import generate_api_key
from app.core.config import Settings
from app.main import create_app
from app.models import ApiKey, Chunk, Document, IndexStatus, KnowledgeBase, RetrievalLog, Tenant
from app.observability.langfuse_client import RetrieveObservability
from app.providers.keyword_search.base import KeywordSearchError
from app.providers.rerank.llm import LLMRerankProvider
from app.schemas.hybrid_search import KeywordMatch
from app.schemas.rerank import RerankCandidate
from app.services.multi_kb_fusion_service import fuse_by_rrf
from app.services.rate_limit_service import QUOTA_ZONE
from tests.rate_support import isolated_rates


DATABASE_URL = os.getenv("RAGNEXUS_TEST_DATABASE_URL")


class FusionTests(unittest.TestCase):
    def test_rank_only_fusion_counts_a_chunk_once_per_kb(self):
        def hit(score):
            return RerankCandidate(chunk_id=uuid4(), document_id=uuid4(), title="t", content="c", score=score)

        shared, first, second = hit(-2), hit(100), hit(-10)
        ranked = fuse_by_rrf([[first, shared, shared], [], [second, shared]], 60)
        self.assertEqual([c.chunk_id for c in ranked], [shared.chunk_id, first.chunk_id, second.chunk_id])
        self.assertEqual([c.score for c in ranked], [2 / 62, 1 / 61, 1 / 61])
        self.assertEqual(shared.score, -2)


@unittest.skipUnless(DATABASE_URL, "set RAGNEXUS_TEST_DATABASE_URL to a migrated test database")
class MultiKbRetrievalTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform == "win32":
            cls.addClassCleanup(asyncio.set_event_loop_policy, asyncio.get_event_loop_policy())
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    async def asyncSetUp(self):
        directory = self.enterContext(TemporaryDirectory())
        settings = Settings(database_url=DATABASE_URL, auth_enabled=True, log_dir=Path(directory),
                            retrieval_mode="vector", hybrid_rrf_k=30, rerank_enabled=False)
        self.app = create_app(settings)
        self.rates = isolated_rates(self.app, self)
        self.embedding = AsyncMock()
        self.embedding.embed.return_value = [[1, 0]]
        self.keyword = AsyncMock()
        self.keyword.name = "elasticsearch"
        self.llm = AsyncMock()
        self.app.dependency_overrides.update({
            get_embedding_provider: lambda: self.embedding, get_keyword_provider: lambda: self.keyword,
            get_llm: lambda: self.llm,
            get_rerank_provider: lambda: LLMRerankProvider(self.llm, chunk_max_chars=100, temperature=0),
        })
        await self.enterAsyncContext(self.app.router.lifespan_context(self.app))
        self.sessions = self.app.state.session_factory
        self.tenant = "multi-" + uuid4().hex
        key, digest, prefix = generate_api_key()
        self.ids = [str(uuid4()) for _ in range(5)]
        self.foreign = str(uuid4())
        async with self.sessions.begin() as session:
            session.add(Tenant(id=self.tenant, name="multi", plan="pro"))
            await session.flush()
            session.add(ApiKey(id=uuid4(), tenant_id=self.tenant, name="test", key_hash=digest, key_prefix=prefix))
            session.add(KnowledgeBase(id=UUID(self.foreign), tenant_id=self.tenant + "-other", name="private"))
            for i, kb_id in enumerate(self.ids):
                scope = dict(tenant_id=self.tenant, kb_id=UUID(kb_id))
                session.add(KnowledgeBase(id=UUID(kb_id), tenant_id=self.tenant, name=f"库{i}",
                                          settings={"synonyms": [{"terms": ["退款"], "expand": [f"词{i}"]}]}))
                await session.flush()
                doc_id = uuid4()
                session.add(Document(id=doc_id, **scope, title="条款", content="条款", source_type="text",
                                     status=IndexStatus.SUCCESS))
                await session.flush()
                session.add(Chunk(id=uuid4(), **scope, document_id=doc_id, title="条款", content=f"片段{i}",
                                  chunk_metadata={}, embedding=[0, 1] if i == 0 else [1, 0]))
        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test/api/v1/",
            headers={"Authorization": "Bearer " + key},
        ))

    async def search(self, **fields):
        return await self.client.post("rag/retrieve", json={
            "user_id": "tester", "query": "退款", "profile": "custom", **fields,
        })

    async def test_parallel_hybrid_with_one_rewrite_embedding_rerank_and_charge(self):
        kb_ids = self.ids[:2]
        barrier = asyncio.Barrier(2)

        async def lexical(**kwargs):
            self.assertIn(str(kwargs["kb_id"]), kb_ids)
            self.assertEqual(kwargs["query"], "退款 词0 词1")
            await asyncio.wait_for(barrier.wait(), timeout=5)
            return []

        def rerank(**kwargs):
            candidates = kwargs["user_payload"]["candidates"]
            return {"rankings": [{"chunk_id": c["chunk_id"], "rerank_score": 0.9 if i else 0.1}
                                 for i, c in enumerate(candidates)]}

        self.keyword.keyword_search.side_effect = lexical
        self.llm.chat_text.return_value = "退款"
        self.llm.chat_json.side_effect = rerank
        # The scalar selector is ignored even when it names another tenant's KB.
        with (
            patch.object(self.rates, "check_retrieve", wraps=self.rates.check_retrieve) as admissions,
            patch("app.services.rag_service.RetrieveObservability", wraps=RetrieveObservability) as observation,
        ):
            response = await self.search(kb_id=self.foreign, kb_ids=kb_ids, top_k=2,
                                         retrieval_options={"mode": "hybrid", "vector_top_k": 12, "rrf_k": 4},
                                         query_options={"enabled": True}, rerank_options={"enabled": True, "top_n": 2})
        admissions.assert_awaited_once()
        trace_metadata = observation.call_args.kwargs["metadata"]
        self.assertEqual((trace_metadata["kb_id"], trace_metadata["kb_ids"]), (kb_ids[0], kb_ids))
        data = response.raise_for_status().json()["data"]
        self.assertEqual((data["kb_id"], data["kb_ids"]), (kb_ids[0], kb_ids))
        hits = data["retrieved_chunks"]
        self.assertEqual([(h["kb_id"], h["kb_name"]) for h in hits], [(kb_ids[1], "库1"), (kb_ids[0], "库0")])
        self.assertEqual([h["score"] for h in hits], [1 / 31] * 2)
        self.assertEqual([h["vector_score"] for h in hits], [1, 0])
        details = data["metadata"]["retrieval"]
        self.assertEqual([details[k] for k in ("fusion", "multi_kb", "kb_count", "per_kb_top_k", "rrf_k",
                                               "fused_count", "vector_count")], ["rrf", True, 2, 12, 30, 2, 2])
        self.embedding.embed.assert_awaited_once_with(["退款 词0 词1"])
        self.llm.chat_text.assert_awaited_once()
        self.llm.chat_json.assert_awaited_once()
        self.assertEqual(self.keyword.keyword_search.await_count, 2)
        self.assertEqual(await self.rates.daily_usage(self.tenant), 1)
        async with self.sessions() as session:
            log = await session.get(RetrievalLog, UUID(data["metadata"]["log_id"]))
            self.assertEqual((str(log.kb_id), log.kb_ids, log.retrieved_chunks), (kb_ids[0], kb_ids, hits))

    async def test_access_limits_and_single_kb_compatibility(self):
        for body in ({}, {"kb_ids": []}, {"kb_ids": [], "kb_id": self.ids[0]}):
            self.assertEqual((await self.search(**body)).json()["code"], 20004)
        for missing in (self.foreign, str(uuid4())):
            denied = await self.search(kb_ids=[self.ids[0], missing])
            self.assertEqual((denied.status_code, denied.json()["code"]), (404, 20009))
            self.assertEqual(denied.json()["data"], {"missing_kb_id": missing})
        self.assertEqual((await self.search(kb_ids=self.ids + [str(uuid4())])).json()["code"], 20013)
        self.embedding.embed.assert_not_awaited()
        self.assertEqual(await self.rates.daily_usage(self.tenant), 0)

        all_five = (await self.search(kb_ids=self.ids, top_k=5)).raise_for_status().json()["data"]
        self.assertEqual([h["kb_id"] for h in all_five["retrieved_chunks"]], self.ids)
        self.assertEqual(await self.rates.daily_usage(self.tenant), 1)
        legacy = (await self.search(kb_id=self.ids[0])).raise_for_status().json()["data"]
        for form in ([self.ids[0]], [self.ids[0]] * 2):
            one = (await self.search(kb_ids=form)).raise_for_status().json()["data"]
            self.assertEqual(one["retrieved_chunks"], legacy["retrieved_chunks"])
            self.assertEqual(one["kb_ids"], [self.ids[0]])
            self.assertNotIn("retrieval", one["metadata"])
        self.assertEqual(legacy["retrieved_chunks"][0]["score"], 0)

        clock = self.enterContext(patch("app.services.rate_limit_service.datetime"))
        now = datetime.now(QUOTA_ZONE)
        for plan, limit in (("standard", 3), ("free", 1)):
            now += timedelta(seconds=1)
            clock.now.return_value = now
            async with self.sessions.begin() as session:
                await session.execute(update(Tenant).where(Tenant.id == self.tenant).values(plan=plan))
            me = (await self.client.get("auth/me")).raise_for_status().json()["data"]
            self.assertEqual(me["limits"]["max_kb_per_retrieve"], limit)
            before = await self.rates.daily_usage(self.tenant)
            denied = await self.search(kb_ids=self.ids[:limit + 1], profile="speed")
            self.assertEqual((denied.status_code, denied.json()["code"]), (403, 20013))
            self.assertEqual(await self.rates.daily_usage(self.tenant), before)
            (await self.search(kb_ids=self.ids[:limit], profile="speed")).raise_for_status()

    async def test_bounded_rerank_keeps_remaining_fused_hits_in_order(self):
        self.app.state.settings.rerank_max_candidates = 2

        def rerank(**kwargs):
            candidates = kwargs["user_payload"]["candidates"]
            self.assertEqual(len(candidates), 2)
            return {"rankings": [{"chunk_id": candidates[1]["chunk_id"], "rerank_score": 1}]}

        self.llm.chat_json.side_effect = rerank
        response = await self.search(kb_ids=self.ids, top_k=5, rerank_options={"enabled": True, "top_n": 1})
        hits = response.raise_for_status().json()["data"]["retrieved_chunks"]
        self.assertEqual([h["kb_id"] for h in hits], [self.ids[1], self.ids[0], *self.ids[2:]])
        self.llm.chat_json.assert_awaited_once()

    async def test_bm25_rehydrates_only_each_knowledge_bases_own_hits(self):
        kb_ids = self.ids[:2]
        async with self.sessions() as session:
            rows = (await session.execute(select(Chunk.kb_id, Chunk.id).where(
                Chunk.tenant_id == self.tenant, Chunk.kb_id.in_([UUID(kb_id) for kb_id in kb_ids]),
            ))).all()
        chunk_ids = dict(rows)

        async def lexical(**kwargs):
            # Include a stale hit from the other KB in both responses.
            return [KeywordMatch(chunk_id=chunk_ids[UUID(kb_id)], bm25_score=score)
                    for kb_id, score in zip(reversed(kb_ids), [99.0, 0.2])]

        self.keyword.keyword_search.side_effect = lexical
        response = await self.search(kb_ids=kb_ids, top_k=2, retrieval_options={"mode": "bm25"})
        data = response.raise_for_status().json()["data"]
        self.embedding.embed.assert_not_awaited()
        self.assertEqual(self.keyword.keyword_search.await_count, 2)
        hits = data["retrieved_chunks"]
        self.assertEqual([(h["kb_id"], h["chunk_id"]) for h in hits],
                         [(i, str(chunk_ids[UUID(i)])) for i in kb_ids])
        self.assertEqual([h["bm25_score"] for h in hits], [0.2, 99])
        self.assertEqual([h["score"] for h in hits], [1 / 31] * 2)
        self.assertEqual(data["metadata"]["retrieval"]["bm25_count"], 2)

    async def test_empty_reason_counts_only_visible_successfully_indexed_chunks(self):
        kb = self.ids[0]
        self.keyword.keyword_search.return_value = []
        missing = (await self.search(kb_id=kb, retrieval_options={"mode": "bm25"})).raise_for_status().json()["data"]
        self.assertEqual(missing["metadata"]["retrieval"]["empty_reason"], "no_chunks_matched")
        async with self.sessions.begin() as session:
            await session.execute(update(Document).where(Document.kb_id == UUID(kb)).values(status=IndexStatus.FAILED))
        # A stale chunk still exists in this KB and the other KBs still have successful documents.
        empty = (await self.search(kb_id=kb)).raise_for_status().json()["data"]
        self.assertEqual(empty["retrieved_chunks"], [])
        self.assertEqual(empty["metadata"]["retrieval"]["empty_reason"], "no_indexed_chunks")
        async with self.sessions() as session:
            log = await session.get(RetrievalLog, UUID(empty["metadata"]["log_id"]))
            self.assertEqual(log.retrieval_metadata, empty["metadata"]["retrieval"])

    async def test_partial_empty_kb_success_is_charged_once(self):
        async def lexical(**kwargs):
            if str(kwargs["kb_id"]) == self.ids[1]:
                raise KeywordSearchError("offline")
            return []

        self.keyword.keyword_search.side_effect = lexical
        partial = (await self.search(kb_ids=self.ids[:2], retrieval_options={"mode": "bm25"})).raise_for_status().json()["data"]
        details = partial["metadata"]["retrieval"]
        self.assertEqual(partial["retrieved_chunks"], [])
        self.assertTrue(details["partial_kb_success"])
        self.assertEqual((details["failed_kb_ids"], details["empty_reason"]), ([self.ids[1]], "no_chunks_matched"))
        self.embedding.embed.assert_not_awaited()
        self.assertEqual(await self.rates.daily_usage(self.tenant), 1)
        response = await self.search(kb_ids=self.ids[:2], retrieval_options={"mode": "hybrid"})
        data = response.raise_for_status().json()["data"]
        self.assertTrue(data["metadata"]["retrieval"]["degraded"])
        self.assertEqual([h["kb_id"] for h in data["retrieved_chunks"]], self.ids[:2])
        self.assertFalse(data["metadata"]["retrieval"]["partial_kb_success"])
        self.assertEqual(await self.rates.daily_usage(self.tenant), 2)
