import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from tests.rate_support import isolated_rates
from tests.indexing_support import upload_and_index
from app.api.dependencies import get_embedding_provider
from app.core.config import Settings
from app.core.auth import TenantContext
from app.api.v1.deps import get_current_tenant
from app.core.error_codes import ErrorCode
from app.core.exceptions import EmbeddingError
from app.main import create_app
from app.models import Chunk, Document, RetrievalLog
from app.providers.embedding.base import EmbeddingProvider
from app.providers.vectorstores.pgvector import PgVectorStore
from app.utils.text_splitter import CharacterTextSplitter


DATABASE_URL = os.getenv("RAGNEXUS_TEST_DATABASE_URL")


class FixedEmbeddingProvider(EmbeddingProvider):
    async def embed(self, texts):
        return [[1.0, 0.0] if "退款" in text else [0.0, 1.0] for text in texts]


class FailingVectorStore(PgVectorStore):
    async def add_chunks(self, chunks):
        await super().add_chunks(chunks)
        raise EmbeddingError("simulated failure after writing chunks")


@unittest.skipUnless(DATABASE_URL, "set RAGNEXUS_TEST_DATABASE_URL to a migrated test database")
class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform == "win32":
            cls.addClassCleanup(asyncio.set_event_loop_policy, asyncio.get_event_loop_policy())
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    async def test_upload_retrieval_tenant_scope_and_failed_index_rollback(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app = create_app(Settings(database_url=DATABASE_URL, top_k=2, log_dir=Path(directory.name)))
        isolated_rates(app, self)
        app.dependency_overrides[get_embedding_provider] = FixedEmbeddingProvider
        tenant = f"test-{uuid4()}"
        app.dependency_overrides[get_current_tenant] = lambda: TenantContext(tenant, "test", plan="pro")
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1/"
            ) as client:
                created = await client.post(
                    "knowledge-bases/create", json={"name": "规则", "tenant_id": tenant}
                )
                self.assertEqual(created.status_code, 200, created.text)
                kb_id = created.json()["data"]["kb_id"]
                upload = {"tenant_id": tenant, "kb_id": kb_id, "title": "规则"}
                for text in ("七天内可以退款", "配送需要三天"):
                    response = await upload_and_index(app, client, upload | {"content": text}, embedding=FixedEmbeddingProvider())
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["data"]["status"], 1)

                failed = await upload_and_index(
                    app, client, upload | {"content": "退款内容不应进入结果"},
                    embedding=FixedEmbeddingProvider(), store=FailingVectorStore,
                )
                failed.raise_for_status()
                self.assertEqual(failed.json()["data"]["status"], 2)
                failed_id = UUID(failed.json()["data"]["document_id"])

                query = {"profile": "custom", "tenant_id": tenant, "kb_id": kb_id, "user_id": "test", "query": "如何退款"}
                retrieved = await client.post("rag/retrieve", json=query)
                self.assertEqual(retrieved.status_code, 200, retrieved.text)
                data = retrieved.json()["data"]
                self.assertEqual(data["metadata"]["top_k"], 2)
                self.assertEqual(data["metadata"]["vector_store"], "pgvector")
                self.assertIsInstance(data["metadata"]["latency_ms"], int)
                self.assertGreaterEqual(data["metadata"]["latency_ms"], 0)
                self.assertEqual([row["content"] for row in data["retrieved_chunks"]], [
                    "七天内可以退款", "配送需要三天",
                ])
                self.assertEqual([row["score"] for row in data["retrieved_chunks"]], [1.0, 0.0])

                other_tenant = query | {"tenant_id": f"other-{tenant}"}
                app.dependency_overrides[get_current_tenant] = lambda: TenantContext(other_tenant["tenant_id"], "test", plan="pro")
                denied = await client.post("rag/retrieve", json=other_tenant)
                self.assertEqual(denied.status_code, 404, denied.text)
                self.assertEqual(denied.json()["code"], ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)

            async with app.state.session_factory() as session:
                document = await session.get(Document, failed_id)
                self.assertEqual(document.status, 2)
                self.assertEqual(document.error_message, ErrorCode.LLM_ERROR.message)
                self.assertIsNone(await session.scalar(select(Chunk.id).where(
                    Chunk.document_id == failed_id
                )))
                saved_log = await session.scalar(select(RetrievalLog).where(
                    RetrievalLog.tenant_id == tenant
                ))
                self.assertEqual(saved_log.user_id, "test")
                self.assertEqual(saved_log.latency_ms, data["metadata"]["latency_ms"])
                self.assertEqual(len(saved_log.retrieved_chunks), 2)

    async def test_upload_keeps_structured_chunks_in_both_indexes(self):
        with TemporaryDirectory() as directory:
            settings = Settings(database_url=DATABASE_URL, log_dir=Path(directory),
                                chunk_size=38, chunk_overlap=4, table_max_rows_per_chunk=1)
            app = create_app(settings)
            isolated_rates(app, self)
            tenant = str(uuid4())
            app.dependency_overrides[get_current_tenant] = lambda: TenantContext(tenant, "test", plan="pro")
            keyword = AsyncMock()
            app.dependency_overrides[get_embedding_provider] = FixedEmbeddingProvider
            async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1/"
            ) as client:
                kb = (await client.post("knowledge-bases/create", json={"name": "表格"})).raise_for_status().json()["data"]["kb_id"]
                rows = [f"| 场景{i} | {i}天 |" for i in range(4)]
                doc = "\n".join(["# 手册", "## 售后", "开篇", "| 类别 | 时效 |", "|---|---|", *rows, "尾注"])
                for title in ("规则.MD", "规则.txt"):
                    result = await upload_and_index(app, client, {"kb_id": kb, "title": title, "content": doc},
                                                    embedding=FixedEmbeddingProvider(), keyword=keyword)
                    detail = result.raise_for_status().json()["data"]
                    self.assertEqual(detail["status"], 1)
                    sent = keyword.add_chunks.await_args.args[0]
                    async with app.state.session_factory() as session:
                        saved = (await session.scalars(select(Chunk).where(Chunk.document_id == UUID(detail["document_id"])))).all()
                    by_id = {chunk.id: chunk for chunk in saved}
                    self.assertEqual(len(saved), detail["chunk_count"])
                    for i, chunk in enumerate(sent):
                        self.assertEqual((chunk.content, chunk.metadata), (by_id[chunk.chunk_id].content, by_id[chunk.chunk_id].chunk_metadata))
                        self.assertEqual((chunk.metadata["position"], chunk.metadata["chunk_index"], chunk.metadata["source_type"]), (i, i, "text"))
                    if title.endswith(".MD"):
                        self.assertTrue(all(c.metadata["heading_path"] == "手册/售后" for c in sent))
                        response = await client.post("rag/retrieve", json={
                            "kb_id": kb, "user_id": "test", "profile": "speed", "query": "售后",
                        })
                        hits = response.raise_for_status().json()["data"]["retrieved_chunks"]
                        self.assertTrue(hits)
                        self.assertTrue(all(hit["metadata"]["heading_path"] == "手册/售后" for hit in hits))
                        tables = [c for c in sent if c.metadata["chunk_type"] == "table"]
                        self.assertEqual([c.metadata["table_part"] for c in tables], [1, 2, 3, 4])
                        self.assertEqual([c.content for c in tables], ["【表头】类别 | 时效\n" + row for row in rows])
                    else:
                        self.assertEqual([c.content for c in sent], CharacterTextSplitter(38, 4).split(doc))

    async def test_rerank_http_contract_and_degraded_retrieval_log(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = Settings(
            database_url=DATABASE_URL, log_dir=Path(directory.name),
            model_base_url="https://embeddings.test/v1", model_api_key="embedding-test-key",
            llm_base_url="https://chat.test/v1", llm_api_key="chat-test-key", llm_model="rank-model",
            rerank_enabled=True, rerank_top_n=1, rerank_max_candidates=2, rerank_chunk_max_chars=5,
        )
        app = create_app(settings)
        isolated_rates(app, self)
        chat_requests = []
        chat_failure = False

        def respond(request):
            body = json.loads(request.content)
            if request.url.host == "embeddings.test":
                self.assertEqual(request.url.path, "/v1/embeddings")
                self.assertEqual(request.headers["authorization"], "Bearer embedding-test-key")
                vectors = {
                    "第一段退款规则全文": [1, 0],
                    "第二段退款规则全文": [1, 1],
                    "配送规则全文": [0, 1],
                    "按规则能否退款": [1, 0],
                }
                return httpx.Response(200, json={"data": [
                    {"index": i, "embedding": vectors[text]} for i, text in enumerate(body["input"])
                ]})

            self.assertEqual(request.url.path, "/v1/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer chat-test-key")
            self.assertEqual(body["model"], "rank-model")
            payload = json.loads(body["messages"][1]["content"])
            chat_requests.append(payload)
            candidates = payload["candidates"]
            content = "invalid JSON" if chat_failure else json.dumps({"rankings": [
                {"chunk_id": candidates[0]["chunk_id"], "rerank_score": 0.1},
                {"chunk_id": candidates[1]["chunk_id"], "rerank_score": 0.98, "reason": "private"},
            ]})
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        def model_client(base_url, api_key, timeout):
            return httpx.AsyncClient(
                base_url=base_url, headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout, transport=httpx.MockTransport(respond),
            )

        tenant = f"test-{uuid4()}"
        app.dependency_overrides[get_current_tenant] = lambda: TenantContext(tenant, "test", plan="pro")
        with patch("app.main.model_client", side_effect=model_client), patch("app.providers.index_clients.model_client", side_effect=model_client):
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1/"
                ) as client:
                    scope = {"tenant_id": tenant}
                    created = await client.post("knowledge-bases/create", json=scope | {"name": "规则"})
                    scope["kb_id"] = created.raise_for_status().json()["data"]["kb_id"]
                    for content in ("第一段退款规则全文", "第二段退款规则全文", "配送规则全文"):
                        uploaded = await upload_and_index(
                            app, client, scope | {"title": "规则", "content": content},
                        )
                        self.assertEqual(uploaded.raise_for_status().json()["data"]["status"], 1)

                    search = scope | {"profile": "custom", "user_id": "test", "query": "按规则能否退款", "top_k": 3}
                    ranked = await client.post("rag/retrieve", json=search)
                    data = ranked.raise_for_status().json()["data"]
                    self.assertNotIn("context_text", data)
                    self.assertEqual(len(data["retrieved_chunks"]), 1)
                    chunk = data["retrieved_chunks"][0]
                    self.assertEqual(chunk["content"], "第二段退款规则全文")
                    self.assertAlmostEqual(chunk["score"], 0.70710678, places=6)
                    self.assertEqual(chunk["rerank_score"], 0.98)
                    self.assertNotIn("reason", chunk)
                    self.assertEqual(data["metadata"]["rerank"], {
                        "enabled": True, "provider": "llm", "llm_provider": "openai_compatible",
                        "model": "rank-model", "top_n": 1, "candidate_count": 2, "degraded": False,
                    })
                    self.assertEqual(chat_requests[0]["query"], search["query"])
                    self.assertEqual(
                        [c["content"] for c in chat_requests[0]["candidates"]],
                        ["第一段退款", "第二段退款"],
                    )

                    disabled = await client.post(
                        "rag/retrieve", json=search | {"rerank_options": {"enabled": False, "top_n": 1}},
                    )
                    disabled_data = disabled.raise_for_status().json()["data"]
                    self.assertEqual(len(disabled_data["retrieved_chunks"]), 3)
                    self.assertFalse(disabled_data["metadata"]["rerank"]["enabled"])
                    self.assertNotIn("rerank_score", disabled_data["retrieved_chunks"][0])
                    self.assertEqual(len(chat_requests), 1)

                    chat_failure = True
                    degraded = await client.post(
                        "rag/retrieve", json=search | {"rerank_options": {"top_n": 3}},
                    )
                    degraded_data = degraded.raise_for_status().json()["data"]
                    self.assertTrue(degraded_data["metadata"]["rerank"]["degraded"])
                    self.assertEqual(
                        [result["content"] for result in degraded_data["retrieved_chunks"]],
                        ["第一段退款规则全文", "第二段退款规则全文", "配送规则全文"],
                    )
                    self.assertTrue(all(
                        "rerank_score" not in result for result in degraded_data["retrieved_chunks"]
                    ))

                    empty = await client.post("knowledge-bases/create", json={"tenant_id": tenant, "name": "空"})
                    empty_kb = empty.raise_for_status().json()["data"]["kb_id"]
                    empty_search = await client.post("rag/retrieve", json=search | {"kb_id": empty_kb})
                    empty_data = empty_search.raise_for_status().json()["data"]
                    self.assertEqual(empty_data["retrieved_chunks"], [])
                    self.assertEqual(empty_data["metadata"]["rerank"]["candidate_count"], 0)
                    self.assertEqual(len(chat_requests), 2)

                async with app.state.session_factory() as session:
                    logs = (await session.scalars(
                        select(RetrievalLog).where(RetrievalLog.tenant_id == tenant)
                    )).all()
                    ranked_log = next(log for log in logs if len(log.retrieved_chunks) == 1)
                    self.assertEqual(ranked_log.top_k, 3)
                    self.assertEqual(ranked_log.retrieved_chunks, [chunk])
                    self.assertNotIn("reason", ranked_log.retrieved_chunks[0])
