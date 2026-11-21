import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from app.api.dependencies import get_embedding_provider, get_vector_store, SessionDependency
from app.core.config import Settings
from app.core.auth import TenantContext
from app.api.v1.deps import get_current_tenant
from app.core.error_codes import ErrorCode
from app.core.exceptions import EmbeddingError
from app.main import create_app
from app.models import Chunk, Document, RetrievalLog
from app.providers.embedding.base import EmbeddingProvider
from app.providers.vectorstores.pgvector import PgVectorStore


DATABASE_URL = os.getenv("RAGNEXUS_TEST_DATABASE_URL")


class FixedEmbeddingProvider(EmbeddingProvider):
    async def embed(self, texts):
        return [[1.0, 0.0] if "退款" in text else [0.0, 1.0] for text in texts]


class FailingVectorStore(PgVectorStore):
    async def add_chunks(self, chunks):
        await super().add_chunks(chunks)
        raise EmbeddingError("simulated failure after writing chunks")


def get_failing_store(session: SessionDependency):
    return FailingVectorStore(session)


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
        app.dependency_overrides[get_embedding_provider] = FixedEmbeddingProvider
        tenant = f"test-{uuid4()}"
        app.dependency_overrides[get_current_tenant] = lambda: TenantContext(tenant, "test")
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
                    response = await client.post("documents/upload", json=upload | {"content": text})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["data"]["status"], 1)

                app.dependency_overrides[get_vector_store] = get_failing_store
                failed = await client.post(
                    "documents/upload", json=upload | {"content": "退款内容不应进入结果"}
                )
                self.assertEqual(failed.status_code, 502, failed.text)
                self.assertEqual(failed.json()["code"], ErrorCode.LLM_ERROR)
                self.assertEqual(failed.json()["data"]["status"], 2)
                failed_id = UUID(failed.json()["data"]["document_id"])
                del app.dependency_overrides[get_vector_store]

                query = {"tenant_id": tenant, "kb_id": kb_id, "user_id": "test", "query": "如何退款"}
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
                app.dependency_overrides[get_current_tenant] = lambda: TenantContext(other_tenant["tenant_id"], "test")
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
        app.dependency_overrides[get_current_tenant] = lambda: TenantContext(tenant, "test")
        with patch("app.main.model_client", side_effect=model_client):
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1/"
                ) as client:
                    scope = {"tenant_id": tenant}
                    created = await client.post("knowledge-bases/create", json=scope | {"name": "规则"})
                    scope["kb_id"] = created.raise_for_status().json()["data"]["kb_id"]
                    for content in ("第一段退款规则全文", "第二段退款规则全文", "配送规则全文"):
                        uploaded = await client.post(
                            "documents/upload", json=scope | {"title": "规则", "content": content},
                        )
                        uploaded.raise_for_status()

                    search = scope | {"user_id": "test", "query": "按规则能否退款", "top_k": 3}
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
