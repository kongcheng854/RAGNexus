import asyncio
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app.api.dependencies import get_embedding_provider, get_keyword_provider, get_rerank_provider
from app.core.config import Settings
from app.core.auth import TenantContext
from app.api.v1.deps import get_current_tenant
from app.models import Chunk, Document, RetrievalLog
from app.main import create_app
from tests.rate_support import isolated_rates
from tests.indexing_support import upload_and_index
from app.reindex_keyword import reindex
from app.providers.embedding.base import EmbeddingProvider
from app.providers.keyword_search.base import KeywordSearchError
from app.providers.rerank.llm import LLMRerankProvider
from app.providers.llm.base import LLMProvider


DATABASE_URL = os.getenv("RAGNEXUS_TEST_DATABASE_URL")
ELASTICSEARCH_URL = os.getenv("RAGNEXUS_TEST_ELASTICSEARCH_URL")


class TestEmbedding(EmbeddingProvider):
    async def embed(self, texts):
        return [[1.0, 0.0] if text == "ZX999" or "语义" in text else [0.0, 1.0] for text in texts]


class TestLLM(LLMProvider):
    async def chat_json(self, *, system_prompt, user_payload, temperature=0.0, timeout_seconds=None):
        return {"rankings": [
            {"chunk_id": chunk["chunk_id"], "rerank_score": 0.95 if "ZX999" in chunk["content"] else 0.1}
            for chunk in user_payload["candidates"]
        ]}


@unittest.skipUnless(DATABASE_URL and ELASTICSEARCH_URL, "set PostgreSQL and Elasticsearch test URLs")
class HybridIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        if sys.platform == "win32":
            cls.addClassCleanup(asyncio.set_event_loop_policy, asyncio.get_event_loop_policy())
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    async def test_dual_write_modes_rerank_downgrade_and_orphan_visibility(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        index = "rag_test_" + uuid4().hex
        settings = Settings(
            database_url=DATABASE_URL, elasticsearch_url=ELASTICSEARCH_URL,
            elasticsearch_index=index, retrieval_mode="hybrid", log_dir=Path(directory.name),
            hybrid_vector_top_k=1, hybrid_bm25_top_k=10, hybrid_top_n=5,
        )
        app = create_app(settings)
        isolated_rates(app, self)
        app.dependency_overrides[get_embedding_provider] = TestEmbedding
        app.dependency_overrides[get_rerank_provider] = lambda: LLMRerankProvider(
            TestLLM(), chunk_max_chars=1000, temperature=0,
        )
        tenant = "test-" + uuid4().hex
        current_tenant = tenant
        app.dependency_overrides[get_current_tenant] = lambda: TenantContext(current_tenant, "test", plan="pro")
        async with app.router.lifespan_context(app):
            keyword = app.state.keyword_provider
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1/",
            ) as client:
                async def upload(body):
                    return await upload_and_index(app, client, body, embedding=TestEmbedding(), keyword=keyword)

                async def create(scope):
                    nonlocal current_tenant
                    current_tenant = scope
                    response = await client.post(
                        "knowledge-bases/create", json={"tenant_id": scope, "name": "混合检索测试"},
                    )
                    return response.raise_for_status().json()["data"]["kb_id"]

                kb_id = await create(tenant)
                scope = {"tenant_id": tenant, "kb_id": kb_id}
                for title, content in (("语义片段", "语义相关文档"), ("错误码条款", "ZX999 表示订单校验失败")):
                    uploaded = await upload(scope | {"title": title, "content": content})
                    self.assertEqual(uploaded.status_code, 200, uploaded.text)

                other_kb = await create(tenant + "-other")
                other = await upload({
                    "tenant_id": tenant + "-other", "kb_id": other_kb,
                    "title": "跨租户", "content": "ZX999 其他租户文档",
                })
                other.raise_for_status()
                current_tenant = tenant
                query = scope | {"profile": "custom", "user_id": "test", "query": "ZX999"}
                result = await client.post("rag/retrieve", json=query)
                data = result.raise_for_status().json()["data"]
                details = data["metadata"]["retrieval"]
                self.assertEqual((details["mode"], details["fusion"]), ("hybrid", "rrf"))
                self.assertEqual(details["vector_count"], 1)
                self.assertEqual(details["bm25_count"], 1)
                self.assertEqual(details["fused_count"], 2)
                self.assertEqual({chunk["retrieval_source"] for chunk in data["retrieved_chunks"]}, {"vector", "bm25"})
                lexical = next(c for c in data["retrieved_chunks"] if c["retrieval_source"] == "bm25")
                self.assertIsNone(lexical.get("vector_score"))
                self.assertIsNone(lexical.get("vector_rank"))
                self.assertGreater(lexical["bm25_score"], 0)
                self.assertAlmostEqual(lexical["score"], 1 / 61)
                self.assertNotIn("context_text", data)

                ranked = await client.post("rag/retrieve", json=query | {
                    "rerank_options": {"enabled": True, "top_n": 1},
                })
                final = ranked.raise_for_status().json()["data"]["retrieved_chunks"][0]
                self.assertEqual(final["retrieval_source"], "bm25")
                self.assertEqual(final["rerank_score"], 0.95)
                self.assertAlmostEqual(final["score"], 1 / 61)

                with patch.object(TestEmbedding, "embed", side_effect=AssertionError("bm25 must skip embeddings")):
                    lexical_only = await client.post("rag/retrieve", json=query | {
                        "retrieval_options": {"mode": "bm25"},
                    })
                lexical_only.raise_for_status()
                self.assertEqual(lexical_only.json()["data"]["retrieved_chunks"][0]["chunk_id"], lexical["chunk_id"])

                with patch.object(keyword, "keyword_search", side_effect=KeywordSearchError("offline")):
                    vector_only = await client.post("rag/retrieve", json=query | {
                        "retrieval_options": {"mode": "vector"},
                    })
                    self.assertEqual(vector_only.status_code, 200, vector_only.text)
                    fallback = await client.post("rag/retrieve", json=query)
                    fallback_data = fallback.raise_for_status().json()["data"]
                    self.assertTrue(fallback_data["metadata"]["retrieval"]["degraded"])
                    self.assertEqual(fallback_data["retrieved_chunks"][0]["retrieval_source"], "vector")
                    self.assertAlmostEqual(fallback_data["retrieved_chunks"][0]["score"], 1.0)
                    bm25_failed = await client.post("rag/retrieve", json=query | {
                        "retrieval_options": {"mode": "bm25"},
                    })
                    self.assertEqual(bm25_failed.status_code, 503)

                with patch.object(TestEmbedding, "embed", side_effect=RuntimeError("vector unavailable")):
                    fallback = (await client.post("rag/retrieve", json=query)).raise_for_status().json()["data"]
                    self.assertEqual(fallback["metadata"]["retrieval"]["degraded_reason"], "vector search failed")
                    self.assertEqual(fallback["retrieved_chunks"][0]["chunk_id"], lexical["chunk_id"])
                    self.assertEqual(fallback["retrieved_chunks"][0]["score"], lexical["bm25_score"])

                original_add = keyword.add_chunks
                async def fail_after_write(chunks):
                    await original_add(chunks)
                    raise KeywordSearchError("partial failure")

                with patch.object(keyword, "add_chunks", side_effect=fail_after_write):
                    failed = await upload(scope | {
                        "title": "回滚测试", "content": "ZX999 回滚文档",
                    })
                self.assertEqual(failed.raise_for_status().json()["data"]["status"], 2)
                failed_id = UUID(failed.json()["data"]["document_id"])
                async with app.state.session_factory() as session:
                    self.assertEqual((await session.get(Document, failed_id)).status, 2)
                    self.assertIsNone(await session.scalar(select(Chunk.id).where(Chunk.document_id == failed_id)))

                # An ES write can outlive a failed SQL commit or a failed compensation.
                # Those entries must never become retrievable.
                with patch.object(keyword, "add_chunks", side_effect=fail_after_write), patch.object(
                    keyword, "delete_by_document_id", side_effect=KeywordSearchError("cleanup unavailable"),
                ):
                    ghost = await upload(scope | {
                        "title": "残留测试", "content": "ZX999 绝不能返回的残留片段",
                    })
                self.assertEqual(ghost.raise_for_status().json()["data"]["status"], 2)
                clean = await client.post("rag/retrieve", json=query)
                clean_data = clean.raise_for_status().json()["data"]
                self.assertTrue(all("残留" not in c["content"] for c in clean_data["retrieved_chunks"]))
                self.assertEqual(clean_data["metadata"]["retrieval"]["bm25_count"], 1)

                current_tenant = tenant + "-other"
                denied = await client.post("rag/retrieve", json=query | {"tenant_id": tenant + "-other"})
                self.assertEqual(denied.status_code, 404)
                current_tenant = tenant

                async with app.state.session_factory() as session:
                    logs = (await session.scalars(
                        select(RetrievalLog).where(RetrievalLog.tenant_id == tenant)
                    )).all()
                    self.assertTrue(any(
                        len(log.retrieved_chunks) == 1 and log.retrieved_chunks[0].get("rerank_score") == 0.95
                        and log.retrieved_chunks[0].get("bm25_score") is not None for log in logs
                    ))
                    rows = (await session.execute(select(Chunk.id).where(
                        Chunk.tenant_id == tenant, Chunk.kb_id == UUID(kb_id)
                    ))).all()
                    self.assertEqual(len(rows), 2)

                settings.retrieval_mode = "vector"
                legacy_upload = await upload(scope | {
                    "title": "历史文件", "content": "ZX999 向量模式新增文档",
                })
                legacy_upload.raise_for_status()
                with patch("app.reindex_keyword.Settings", return_value=settings):
                    await reindex(tenant, UUID(kb_id))
                    await reindex(tenant, UUID(kb_id))
                backfilled = await client.post("rag/retrieve", json=query | {
                    "retrieval_options": {"mode": "bm25"},
                })
                backfilled_data = backfilled.raise_for_status().json()["data"]
                self.assertEqual(backfilled_data["metadata"]["retrieval"]["bm25_count"], 2)
                self.assertTrue(any(
                    item["content"] == "ZX999 向量模式新增文档"
                    for item in backfilled_data["retrieved_chunks"]
                ))

            async with httpx.AsyncClient(base_url=ELASTICSEARCH_URL) as es:
                mapping = (await es.get(f"/{index}/_mapping")).raise_for_status().json()
                self.assertEqual(mapping[index]["mappings"]["properties"]["content"]["search_analyzer"], "ik_smart")
                tokens = (await es.post(f"/{index}/_analyze", json={
                    "analyzer": "ik_smart", "text": "中华人民共和国",
                })).raise_for_status().json()["tokens"]
                self.assertTrue(tokens)
                await es.delete(f"/{index}")
