import asyncio
import json
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4, uuid5

import httpx
from celery import Celery
from celery.contrib.testing.worker import start_worker
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.dependencies import get_keyword_provider, get_vector_store
from app.api.v1.deps import get_current_tenant
from app.core.auth import TenantContext
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.models import Chunk, Document, RetrievalLog, Tenant
from app.observability.langfuse_client import langfuse_connection
from app.services.index_dispatch import recover_pending
from app.providers.keyword_search.base import KeywordSearchError
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider
from app.providers.rerank.noop import NoopRerankProvider
from app.schemas.hybrid_search import KeywordMatch
from tests.rate_support import isolated_rates
from app.tasks.indexing import index_document_task


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kwargs):
    return "JSON"


@unittest.skipUnless(find_spec("aiosqlite"), "install .[test] for isolated lifecycle tests")
class DocumentLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        url = "sqlite+aiosqlite:///" + (Path(directory.name) / "test.db").as_posix()
        engine = create_async_engine(url, poolclass=NullPool)
        self.addAsyncCleanup(engine.dispose)
        async with engine.begin() as db:
            await db.run_sync(Base.metadata.create_all)
        sessions = self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.embedding = AsyncMock()
        self.embedding.embed.return_value = [[1.0, 0.0]]
        self.keyword = AsyncMock()
        self.keyword.name = "elasticsearch"
        self.tenant = "one"

        async def session_dependency():
            async with sessions() as session:
                yield session

        @asynccontextmanager
        async def clients(settings):
            yield self.embedding, self.keyword

        settings = Settings(auth_enabled=False, retrieval_mode="bm25", query_rewrite_enabled=False, rerank_enabled=False)
        app = self.app = create_app(settings)
        self.plan = "pro"
        self.rates = isolated_rates(app, self)
        app.state.embedding_provider = self.embedding
        app.state.llm = None
        app.state.rerank_provider = NoopRerankProvider()
        app.dependency_overrides[get_session] = session_dependency
        app.dependency_overrides[get_keyword_provider] = lambda: self.keyword
        app.dependency_overrides[get_current_tenant] = lambda: TenantContext(self.tenant, self.tenant, plan=self.plan)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1/")
        self.addAsyncCleanup(self.client.aclose)
        self.enterContext(patch("app.tasks.indexing.standalone_session", sessions))
        self.enterContext(patch("app.tasks.indexing.index_clients", clients))
        self.publish = self.enterContext(patch("app.services.index_dispatch.index_document_task.delay"))
        created = await self.client.post("knowledge-bases/create", json={"name": "例库"})
        self.kb = created.raise_for_status().json()["data"]["kb_id"]
        self.kb_path = "knowledge-bases/" + self.kb

    async def upload(self):
        response = await self.client.post("documents/upload", json={"kb_id": self.kb, "title": "样例", "content": "原文"})
        data = response.raise_for_status().json()["data"]
        self.assertEqual((data["status"], data["chunk_count"]), (3, 0))
        self.publish.assert_called_with(data["document_id"])
        return data["document_id"]

    async def deliver(self, identifier, retries=0):
        # Use the bound Celery task with fresh worker resources, independently of the ASGI request loop.
        return await asyncio.to_thread(index_document_task.apply, args=(identifier,), retries=retries)

    async def test_failed_retry_success_retrieval_and_cleanup(self):
        identifier = await self.upload()
        path = "documents/" + identifier
        self.embedding.embed.assert_not_called()
        self.assertEqual((await self.client.delete(path)).status_code, 409)
        self.assertEqual((await self.client.post(path + "/reindex")).status_code, 409)
        self.assertEqual((await self.client.delete(self.kb_path)).status_code, 409)
        self.tenant = "other"
        for method, url in [("GET", path), ("DELETE", path), ("POST", path + "/reindex"),
                            ("GET", self.kb_path), ("DELETE", self.kb_path)]:
            self.assertEqual((await self.client.request(method, url)).status_code, 404)
        self.tenant = "one"
        self.keyword.add_chunks.side_effect = KeywordSearchError("offline")
        with patch.object(index_document_task, "retry", wraps=index_document_task.retry) as retry:
            failed = await self.deliver(identifier)
            self.assertTrue(failed.failed())
            self.assertEqual(retry.call_count, 2)
        detail = (await self.client.get(path)).json()["data"]
        self.assertEqual((detail["status"], detail["chunk_count"]), (2, 0))
        self.assertTrue(detail["error_message"])
        tree = (await self.client.get("knowledge-bases/tree")).json()["data"]
        doc = tree[0]["knowledge_bases"][0]["documents"][0]
        self.assertEqual((doc["status"], doc["error_message"]), ("FAILED", detail["error_message"]))

        self.keyword.add_chunks.side_effect = None
        self.assertEqual((await self.client.post(path + "/reindex")).json()["data"]["status"], 3)
        self.assertTrue((await self.deliver(identifier)).successful())
        self.keyword.add_chunks.reset_mock()
        self.assertTrue((await self.deliver(identifier)).successful())
        self.keyword.add_chunks.assert_not_awaited()
        detail = (await self.client.get(path)).json()["data"]
        self.assertEqual((detail["status"], detail["chunk_count"], detail["error_message"]), (1, 1, None))
        accepted = (await self.client.post(path + "/reindex")).raise_for_status().json()["data"]
        self.assertEqual((accepted["status"], accepted["chunk_count"]), (3, 0))
        self.assertEqual((await self.client.post(path + "/reindex")).status_code, 409)
        self.assertTrue((await self.deliver(identifier)).successful())

        settings = {"synonyms": [{"terms": ["问"], "expand": ["原文"]}]}
        for invalid in [{}, {"settings": None}, {"settings": {"synonyms": [{"terms": [], "expand": ["词"]}]}}]:
            self.assertEqual((await self.client.patch(self.kb_path, json=invalid)).json()["code"], 20004)
        for config in [{"rewrite_hint": "旧值"}, settings]:
            (await self.client.patch(self.kb_path, json={"settings": config})).raise_for_status()
        kb = (await self.client.get(self.kb_path)).json()["data"]
        self.assertEqual((kb["document_count"], kb["settings"]), (1, settings))
        self.keyword.keyword_search.return_value = [KeywordMatch(chunk_id=uuid5(UUID(identifier), "0"), bm25_score=1)]
        query = {"profile": "custom", "kb_id": self.kb, "user_id": "test", "query": "问"}
        result = (await self.client.post("rag/retrieve", json=query)).raise_for_status().json()["data"]
        self.assertEqual(result["retrieved_chunks"][0]["content"], "原文")
        self.assertEqual(result["metadata"]["query_processing"]["search_query"], "问 原文")
        (await self.client.delete(path)).raise_for_status()
        self.keyword.delete_by_document_id.assert_awaited_with(UUID(identifier))
        result = (await self.client.post("rag/retrieve", json=query)).raise_for_status().json()["data"]
        self.assertEqual(result["retrieved_chunks"], [])
        self.assertEqual((await self.client.get(path)).status_code, 404)
        another = await self.upload()
        self.assertTrue((await self.deliver(another)).successful())
        (await self.client.delete(self.kb_path)).raise_for_status()
        self.keyword.delete_by_document_id.assert_awaited_with(UUID(another))
        self.assertEqual((await self.client.get("knowledge-bases/tree")).json()["data"], [])
        self.assertEqual((await self.client.post("rag/retrieve", json=query)).status_code, 404)

    @unittest.skipUnless(find_spec("mammoth") and find_spec("pymupdf"), "install .[parsers] for file parsing tests")
    async def test_file_upload_validation_and_reparse(self):
        from scripts.verify_parser_12 import sample_docx, sample_pdf
        from app.providers.parsers.pdf import PdfParser

        with TemporaryDirectory() as tmp:
            self.app.state.settings.document_storage_path = Path(tmp)
            self.app.state.settings.document_max_size_mb = 1

            async def send(name, data, title=None):
                fields = {"kb_id": self.kb}
                if title:
                    fields["title"] = title
                return await self.client.post("documents/upload", data=fields, files={"file": (name, data)})

            for name in ("bad.zip", "../bad.pdf", "old.doc"):
                self.assertEqual((await send(name, b"bad")).json()["code"], 20004)
            self.assertEqual((await send("big.pdf", b"x" * (1024 * 1024 + 1))).status_code, 413)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.embedding.embed.side_effect = lambda texts: [[1, 0] for _ in texts]
            samples = [("word.docx", sample_docx("orange"), "orange", "docx"),
                       ("file.pdf", sample_pdf("blue"), "blue", "pdf"), ("notes.txt", b"plain", "plain", "text"),
                       ("terms.md", b"# Heading\ncontent", "content", "markdown")]
            for name, data, marker, kind in samples:
                result = (await send(name, data)).raise_for_status().json()["data"]
                doc_id = result["document_id"]
                self.assertTrue((await self.deliver(doc_id)).successful())
                async with self.sessions() as session:
                    saved = await session.get(Document, UUID(doc_id))
                    self.assertEqual(saved.source_type, kind)
                    self.assertEqual(saved.title, Path(name).stem)
                    self.assertEqual(saved.source_filename, name)
                    self.assertEqual(Path(saved.source_file_path).read_bytes(), data)
                    self.assertIn(marker, saved.content)
                    chunk = await session.scalar(select(Chunk).where(Chunk.document_id == UUID(doc_id)))
                    self.assertEqual(chunk.chunk_metadata["parser"], saved.parser_metadata["parser"])
                if kind == "pdf":
                    source = Path(saved.source_file_path)
                    source.write_bytes(sample_pdf("new text"))
                    endpoint = f"documents/{doc_id}/reindex"
                    (await self.client.post(endpoint)).raise_for_status()
                    with patch.object(PdfParser, "parse", side_effect=AssertionError("unexpected parse")):
                        self.assertTrue((await self.deliver(doc_id)).successful())
                    self.assertIn("blue", self.embedding.embed.await_args.args[0][0])
                    (await self.client.post(endpoint, params={"reparse": "true"})).raise_for_status()
                    self.assertTrue((await self.deliver(doc_id)).successful())
                    self.assertIn("new text", self.embedding.embed.await_args.args[0][0])
                (await self.client.delete(f"documents/{doc_id}")).raise_for_status()
                self.assertFalse(Path(saved.source_file_path).exists())
            for name, data, reason in [("scan.pdf", sample_pdf(""), "疑似扫描件"), ("bad.docx", b"broken", "另存为 docx")]:
                doc_id = (await send(name, data)).raise_for_status().json()["data"]["document_id"]
                self.assertTrue((await self.deliver(doc_id)).failed())
                detail = (await self.client.get("documents/" + doc_id)).json()["data"]
                self.assertEqual(detail["status"], 2)
                self.assertIn(reason, detail["error_message"])
            (await self.client.delete(self.kb_path)).raise_for_status()
            self.assertFalse(any(p.is_file() for p in Path(tmp).rglob("*")))

    async def test_plan_profiles_and_daily_usage(self):
        self.plan = "free"
        vector = AsyncMock()
        vector.name = "pgvector"
        vector.similarity_search.return_value = []
        self.app.dependency_overrides[get_vector_store] = lambda: vector
        self.keyword.keyword_search.return_value = []
        request = {"kb_id": self.kb, "query": "退款", "user_id": "tester"}
        for profile in ({}, {"profile": "balanced"}):
            denied = await self.client.post("rag/retrieve", json=request | profile)
            self.assertEqual((denied.status_code, denied.json()["code"]), (403, 20013))
        speed = await self.client.post("rag/retrieve", json=request | {"profile": "speed", "top_k": 80})
        metadata = speed.raise_for_status().json()["data"]["metadata"]
        self.assertEqual(metadata["top_k"], 3)
        self.assertEqual(metadata["tenant_policy"], {
            "plan": "free", "retrieve_profile": "speed", "effective_mode": "vector",
            "effective_query_rewrite": False, "effective_rerank": False,
        })
        self.plan = "standard"
        for option in ("rerank_options", "query_options"):
            denied = await self.client.post("rag/retrieve", json=request | {"profile": "custom", option: {"enabled": True}})
            self.assertEqual(denied.json()["code"], 20013)
        self.plan = "pro"
        ranker = AsyncMock()
        ranker.name = "llm"
        ranker.rerank.return_value = []
        self.app.state.rerank_provider = ranker
        quality = await self.client.post("rag/retrieve", json=request | {"profile": "quality"})
        metadata = quality.raise_for_status().json()["data"]["metadata"]
        self.assertEqual(metadata["top_k"], 8)
        self.assertEqual(metadata["tenant_policy"], {
            "plan": "pro", "retrieve_profile": "quality", "effective_mode": "hybrid",
            "effective_query_rewrite": True, "effective_rerank": True,
        })
        ranker.rerank.assert_awaited_once()
        me = (await self.client.get("auth/me")).raise_for_status().json()["data"]
        self.assertEqual(me["usage"], {"kb_count": 1, "retrieve_daily_count": 2})
        self.tenant = "other"
        me = (await self.client.get("auth/me")).raise_for_status().json()["data"]
        self.assertEqual(me["usage"], {"kb_count": 0, "retrieve_daily_count": 0})

    async def test_free_catalog_limits_and_reindex_at_document_limit(self):
        self.plan = "free"
        denied = await self.client.post("knowledge-bases/create", json={"name": "第二个库"})
        self.assertEqual(denied.json()["code"], 20014)
        self.assertIn("知识库", denied.json()["msg"])
        active = await self.upload()
        upload = {"kb_id": self.kb, "title": "下一份", "content": "文本"}
        denied = await self.client.post("documents/upload", json=upload)
        self.assertEqual(denied.json()["code"], 20014)
        self.assertIn("索引", denied.json()["msg"])
        failed = uuid4()
        async with self.sessions() as session, session.begin():
            for identifier in [failed] + [uuid4() for _ in range(28)]:
                session.add(Document(id=identifier, tenant_id=self.tenant, kb_id=UUID(self.kb),
                                     title="旧文档", content="文本", source_type="text", status=2))
        reindex = f"documents/{failed}/reindex"
        self.assertEqual((await self.client.post(reindex)).json()["code"], 20014)
        async with self.sessions() as session, session.begin():
            await session.execute(update(Document).where(Document.id == UUID(active)).values(status=1))
        denied = await self.client.post("documents/upload", json=upload)
        self.assertEqual(denied.json()["code"], 20014)
        self.assertIn("30", denied.json()["msg"])
        self.assertEqual((await self.client.post(reindex)).raise_for_status().json()["data"]["status"], 3)

    async def test_langfuse_trace_and_feedback_contract(self):
        identifier = await self.upload()
        self.assertTrue((await self.deliver(identifier)).successful())
        match = uuid5(UUID(identifier), "0")
        self.keyword.keyword_search.return_value = [KeywordMatch(chunk_id=match, bm25_score=1)]
        (await self.client.patch(self.kb_path, json={"settings": {"synonyms": [
            {"terms": ["问"], "expand": ["同义词"]},
        ]}})).raise_for_status()
        query = {"profile": "custom", "kb_id": self.kb, "user_id": "tester", "query": "问"}
        batches, scores = [], []
        failures = set()

        def ingest(request):
            path = request.url.path
            body = json.loads(request.content)
            if path in failures:
                return httpx.Response(503, json={"message": "offline"})
            if path == "/api/public/ingestion":
                events = body["batch"]
                batches.append(events)
                return httpx.Response(207, json={
                    "successes": [{"id": event["id"], "status": 201} for event in events], "errors": [],
                })
            self.assertEqual(path, "/api/public/scores")
            scores.append(body)
            return httpx.Response(201, json={"id": "score-test"})

        settings = Settings(langfuse_enabled=True, langfuse_host="http://langfuse.test",
                            langfuse_public_key="pk-test", langfuse_secret_key="sk-test")
        http = httpx.Client(transport=httpx.MockTransport(ingest))
        with patch("app.observability.langfuse_client.httpx.Client", return_value=http) as factory:
            self.app.state.langfuse = self.enterContext(langfuse_connection(settings))
        factory.assert_called_once_with(trust_env=False, timeout=3)

        result = (await self.client.post("rag/retrieve", json=query)).raise_for_status().json()["data"]
        metadata = result["metadata"]
        trace_id, log_id = metadata["trace_id"], metadata["log_id"]
        self.assertTrue(trace_id)
        trace, *spans = [event["body"] for event in batches[0]]
        self.assertEqual((trace["id"], trace["name"]), (trace_id, "rag_retrieve"))
        self.assertEqual(trace["metadata"], dict(tenant_id=self.tenant, kb_id=self.kb, kb_ids=[self.kb], user_id="tester",
                                                 plan="pro", profile="custom", log_id=log_id))
        self.assertEqual(trace["output"], [{"chunk_id": str(match), "score": result["retrieved_chunks"][0]["score"]}])
        self.assertEqual([s["name"] for s in spans], ["query_processing", "retrieval", "rerank"])
        self.assertTrue(all(s["traceId"] == trace_id and s["startTime"] <= s["endTime"] for s in spans))
        self.assertEqual(spans[0]["output"]["search_query"], "问 同义词")
        self.assertEqual(spans[1]["output"], dict(mode="bm25", vector_count=0, bm25_count=1, degraded=False))
        self.assertFalse(spans[2]["output"]["enabled"])
        async with self.sessions() as session:
            log = await session.get(RetrievalLog, UUID(log_id))
            self.assertEqual((log.trace_id, log.profile, log.effective_query, log.search_query),
                             (trace_id, "custom", "问", "问 同义词"))

        # Suppression and synonym overrides apply to this request only.
        async with httpx.AsyncClient(base_url="http://model.test", transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "问"}}]}))) as model:
            self.app.state.llm = OpenAICompatibleLLMProvider(model, "test")
            offline = await self.client.post("rag/retrieve", headers={"X-RAGNexus-Offline-Eval": "true"}, json=query | {
                "query_options": {"enabled": True, "synonym_enabled": False}})
        diag = offline.raise_for_status().json()["data"]["metadata"]
        self.assertEqual((diag["trace_id"], diag["offline"]["langfuse_enabled"], diag["offline"]["application_model_calls"]),
                         (None, False, 1))
        self.assertFalse(diag["query_processing"]["synonym_applied"])
        self.assertEqual(len(batches), 1)
        async with self.sessions() as session:
            self.assertIsNotNone(await session.get(RetrievalLog, UUID(diag["log_id"])))
        normal = (await self.client.post("rag/retrieve", json=query)).raise_for_status().json()["data"]["metadata"]
        self.assertTrue(normal["trace_id"])
        self.assertTrue(normal["query_processing"]["synonym_applied"])
        self.assertNotIn("offline", normal)

        payload = {"trace_id": trace_id, "score": 4, "comment": "相关"}
        for score, identifiers in ((2, {}), (4, {"log_id": log_id})):
            response = await self.client.post("rag/feedback", json=payload | identifiers | {"score": score})
            self.assertEqual(response.raise_for_status().json()["data"], {
                "feedback_id": "score-test", "log_id": log_id, "trace_id": trace_id, "score": score,
            })
        self.assertEqual(scores, [dict(name="user_feedback", value=score, comment="相关", traceId=trace_id)
                                  for score in (2, 4)])
        for change in ({"log_id": str(uuid4())}, {"log_id": log_id, "trace_id": "different"}):
            denied = await self.client.post("rag/feedback", json=payload | change)
            self.assertEqual(denied.json()["code"], 20021)
        self.tenant = "other"
        for identifiers in ({}, {"log_id": log_id}):
            denied = await self.client.post("rag/feedback", json=payload | identifiers)
            self.assertEqual((denied.status_code, denied.json()["code"]), (403, 20021))
        self.tenant = "one"
        for score in (0, 6, 2.5, True, "4"):
            invalid = await self.client.post("rag/feedback", json=payload | {"score": score})
            self.assertEqual((invalid.status_code, invalid.json()["code"]), (422, 20022))
        self.assertEqual(len(scores), 2)
        failures.update(("/api/public/scores", "/api/public/ingestion"))
        self.assertEqual((await self.client.post("rag/feedback", json=payload)).json()["code"], 20020)
        data = (await self.client.post("rag/retrieve", json=query)).raise_for_status().json()["data"]
        self.assertIsNone(data["metadata"]["trace_id"])
        self.assertTrue(data["retrieved_chunks"])
        failures.clear()
        missing = await self.client.post("rag/retrieve", json=query | {"kb_id": str(uuid4())})
        self.assertEqual(missing.status_code, 404)
        self.assertIn("error_type", batches[-1][0]["body"]["metadata"])
        # Reporting a SQL failure after a trace was accepted must not duplicate its child spans.
        with patch("app.repositories.catalog.add_retrieval_log", side_effect=RuntimeError("write failed")):
            failed = await self.client.post("rag/retrieve", json=query)
            self.assertEqual((failed.status_code, failed.json()["code"]), (500, 50000))
        self.assertEqual(len(batches[-1]), 1)
        self.assertEqual(batches[-1][0]["body"]["id"], batches[-2][0]["body"]["id"])
        self.assertEqual(batches[-1][0]["body"]["metadata"]["error_type"], "RuntimeError")

    async def test_lost_feedback_response_retries_the_same_score(self):
        logs = [RetrievalLog(id=uuid4(), tenant_id=self.tenant, kb_id=UUID(self.kb), user_id="tester",
                             query="问", top_k=3, vector_store="pgvector", latency_ms=1,
                             retrieved_chunks=[], trace_id=str(uuid4())) for _ in range(2)]
        async with self.sessions() as session, session.begin():
            session.add_all(logs)
        stored = {}
        attempts = []

        def save(request):
            data = json.loads(request.content)
            identifier = data["id"]
            stored[identifier] = data
            attempts.append(identifier)
            if len(attempts) == 1:
                raise httpx.ReadTimeout("response lost after commit", request=request)
            return httpx.Response(201, json={"id": identifier})

        settings = Settings(_env_file=None, langfuse_enabled=True, langfuse_public_key="pk-test",
                            langfuse_secret_key="sk-test", langfuse_host="http://langfuse.test")
        http = httpx.Client(transport=httpx.MockTransport(save))
        with patch("app.observability.langfuse_client.httpx.Client", return_value=http):
            self.app.state.langfuse = self.enterContext(langfuse_connection(settings))

        def submit(log, token):
            return self.client.post("rag/feedback", headers={"X-Feedback-Submission-Id": token},
                                    json={"trace_id": log.trace_id, "log_id": str(log.id), "score": 4})

        token = str(uuid4())
        self.assertEqual((await submit(logs[0], token)).json()["code"], 20020)
        retry = (await submit(logs[0], token)).raise_for_status().json()["data"]
        self.assertEqual(attempts, [retry["feedback_id"], retry["feedback_id"]])
        self.assertEqual(len(stored), 1)
        (await submit(logs[0], str(uuid4()))).raise_for_status()
        (await submit(logs[1], token)).raise_for_status()
        self.assertEqual(len(stored), 3)

    async def test_tracing_disabled_keeps_local_log_and_rejects_feedback(self):
        self.keyword.keyword_search.return_value = []
        response = await self.client.post("rag/retrieve", json={
            "profile": "custom", "kb_id": self.kb, "user_id": "tester", "query": "原话",
        })
        metadata = response.raise_for_status().json()["data"]["metadata"]
        self.assertIsNone(metadata["trace_id"])
        async with self.sessions() as session:
            log = await session.get(RetrievalLog, UUID(metadata["log_id"]))
            self.assertEqual((log.trace_id, log.search_query, log.effective_query), (None, "原话", "原话"))
        unavailable = await self.client.post("rag/feedback", json={"trace_id": "inactive", "score": 2})
        self.assertEqual((unavailable.status_code, unavailable.json()["code"]), (503, 20020))

    async def test_batch_rebuilds_legacy_chunks_and_continues_after_worker_failure(self):
        from scripts.reindex_knowledge_base import rebuild

        kb = UUID(self.kb)
        broken, good, busy, failed, archived = (UUID(int=i) for i in range(1, 6))
        table = "| 类别 | 天数 |\n|---|---|\n| 退款 | 7 |"
        docs = [(broken, 1, "broken"), (good, 1, "# 手册\n## 退款\n" + table),
                (busy, 3, "处理中"), (failed, 2, "已失败"), (archived, 1, "")]
        async with self.sessions() as session, session.begin():
            session.add(Tenant(id=self.tenant, name="测试", plan="pro"))
            for identifier, status, source in docs:
                session.add(Document(id=identifier, kb_id=kb, tenant_id=self.tenant, title="规则.md",
                                     status=status, source_type="text", content=source))
            await session.flush()
            for identifier in (good, archived):
                session.add(Chunk(id=uuid4(), document_id=identifier, kb_id=kb, tenant_id=self.tenant,
                                  title="规则.md", content="旧片段", chunk_metadata={"position": 0}, embedding=[1, 0]))

        def embed(texts):
            if "broken" in texts:
                raise ValueError("simulated worker failure")
            return [[1, 0] for _ in texts]

        self.embedding.embed.side_effect = embed
        queue = Celery("reindex_test", broker="memory://", backend="cache+memory://", set_as_current=False)
        self.addCleanup(queue.close)
        task = queue.tasks[index_document_task.name]
        with start_worker(queue, pool="solo", perform_ping_check=False), patch("app.services.index_dispatch.index_document_task", task):
            async with self.sessions() as session:
                counts = await rebuild(session, self.keyword, kb, None, [], 15, auth_enabled=True)
                self.assertEqual(counts, {"success": 1, "failed": 1, "skipped": 3})
                with self.assertRaisesRegex(ValueError, "租户"):
                    await rebuild(session, self.keyword, kb, "other", [], 15, auth_enabled=True)
                counts = await rebuild(session, self.keyword, kb, self.tenant, [uuid4(), failed, failed, busy], 15, auth_enabled=True)
                self.assertEqual(counts, {"success": 1, "failed": 1, "skipped": 1})
        async with self.sessions() as session:
            chunk = (await session.scalars(select(Chunk).where(Chunk.document_id == good))).one()
            self.assertEqual((chunk.content, chunk.chunk_metadata["heading_path"], chunk.chunk_metadata["chunk_type"]),
                             (table, "手册/退款", "table"))
            self.assertEqual((await session.get(Document, broken)).status, 2)
            self.assertEqual((await session.get(Document, good)).status, 1)
            self.assertEqual(await session.scalar(select(Chunk.content).where(Chunk.document_id == archived)), "旧片段")
        self.keyword.keyword_search.return_value = [KeywordMatch(chunk_id=chunk.id, bm25_score=1)]
        hit = (await self.client.post("rag/retrieve", json={"kb_id": self.kb, "user_id": "test", "query": "退款",
                        "profile": "custom", "retrieval_options": {"mode": "bm25"}})).raise_for_status()
        self.assertEqual(hit.json()["data"]["retrieved_chunks"][0]["metadata"],
                         {"heading_path": "手册/退款", "chunk_type": "table", "table_part": 1})

    async def test_worker_receives_recovered_dispatch(self):
        # Simulate a process exit after SQL commit and before Redis publication.
        with patch("app.services.document_service.dispatch_document", new_callable=AsyncMock):
            response = await self.client.post("documents/upload", json={
                "kb_id": self.kb, "title": "待投递", "content": "原文",
            })
        identifier = response.raise_for_status().json()["data"]["document_id"]
        self.publish.assert_not_called()
        async with self.sessions() as session, session.begin():
            await session.execute(update(Document).where(Document.id == UUID(identifier)).values(
                updated_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            ))
        # Exercise Celery's serializer, registration, consumer and worker using an isolated broker.
        queue = Celery("test_dispatch", broker="memory://", backend="cache+memory://", set_as_current=False)
        self.addCleanup(queue.close)
        task = queue.tasks[index_document_task.name]
        with start_worker(queue, pool="solo", perform_ping_check=False), patch("app.services.index_dispatch.index_document_task", task):
            await recover_pending(self.sessions)
            await recover_pending(self.sessions)
            async with asyncio.timeout(10):
                while True:
                    data = (await self.client.get("documents/" + identifier)).raise_for_status().json()["data"]
                    if data["status"] != 3:
                        break
                    await asyncio.sleep(0.05)
        self.assertEqual((data["status"], data["chunk_count"]), (1, 1))
        self.embedding.embed.assert_awaited_once()

    async def test_publish_failure_leaves_recoverable_document(self):
        self.publish.side_effect = ConnectionError("broker offline")
        failed = await self.client.post("documents/upload", json={"kb_id": self.kb, "title": "样例", "content": "原文"})
        self.assertEqual(failed.status_code, 503)
        identifier = failed.json()["data"]["document_id"]
        detail = (await self.client.get("documents/" + identifier)).json()["data"]
        self.assertEqual(detail["status"], 2)
        self.assertTrue(detail["error_message"])
