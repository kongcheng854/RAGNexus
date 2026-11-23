import asyncio
import json
import unittest
from contextlib import nullcontext
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import httpx

from app.api.dependencies import (
    get_embedding_provider, get_keyword_provider, get_llm, get_rerank_provider, get_vector_store,
)
from app.core.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.providers.rerank.noop import NoopRerankProvider
from app.models import KnowledgeBase
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider
from app.providers.query.base import QueryOptions
from app.providers.query.pipeline import QueryPipeline
from app.schemas.rag import RetrievalInput
from app.services.rag_service import retrieve
from app.tenant.plan_presets import PLAN_PRESETS
from tests.rate_support import isolated_rates


def knowledge_base(term: str, expansion: str) -> KnowledgeBase:
    return KnowledgeBase(
        id=uuid4(), tenant_id="tenant", name="招聘", description="人事",
        settings={"synonyms": [{"terms": [term], "expand": [expansion]}], "rewrite_hint": "试用期"},
    )


class QueryProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_text_rewrite_then_multiple_ordered_expansions(self):
        kb = knowledge_base("背调", "背景调查")
        kb.settings["synonyms"].append({"terms": ["hr"], "expand": ["背景调查", "问题清单"]})
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": " HR背调流程 "}}]})

        async with httpx.AsyncClient(base_url="https://model.test", transport=httpx.MockTransport(respond)) as client:
            pipeline = QueryPipeline(Settings(query_rewrite_enabled=False), OpenAICompatibleLLMProvider(client, "test"))
            result = await pipeline.process("一般都问啥", kb, QueryOptions(enabled=True))

        self.assertEqual(len(requests), 1)
        body = requests[0]
        self.assertEqual((body["max_tokens"], body["temperature"]), (128, 0))
        self.assertNotIn("response_format", body)
        self.assertEqual(json.loads(body["messages"][1]["content"]), {
            "query": "一般都问啥", "kb_name": "招聘", "description": "人事\n试用期",
        })
        self.assertEqual(result.raw_query, "一般都问啥")
        self.assertEqual(result.effective_query, "HR背调流程")
        self.assertEqual(result.search_query, "HR背调流程 背景调查 问题清单")
        self.assertFalse(result.degraded)
        self.assertEqual(result.synonym_expansions, ["背景调查", "问题清单"])

    async def test_request_disables_global_rewrite_and_settings_are_scoped(self):
        llm = AsyncMock()
        pipeline = QueryPipeline(Settings(query_rewrite_enabled=True), llm)
        for expansion in ["人才调查", "供应商调查"]:
            result = await pipeline.process("背调", knowledge_base("背调", expansion), QueryOptions(enabled=False))
            self.assertEqual(result.search_query, f"背调 {expansion}")
            self.assertTrue(result.synonym_applied)
            self.assertEqual(result.strategy, "noop")
        llm.chat_text.assert_not_called()

    async def test_global_rewrite_degrades_and_still_expands_on_failure(self):
        async def slow(**kwargs):
            await asyncio.sleep(10)

        providers = [None, Mock(chat_text=AsyncMock(return_value=" ")),
                     Mock(chat_text=AsyncMock(side_effect=RuntimeError())), Mock(chat_text=slow)]
        for provider in providers:
            pipeline = QueryPipeline(Settings(query_rewrite_enabled=True, query_rewrite_timeout_ms=5), provider)
            result = await pipeline.process("背调", knowledge_base("背调", "流程"), None)
            self.assertTrue(result.degraded)
            self.assertTrue(result.degraded_reason)
            self.assertEqual(result.effective_query, result.raw_query)
            self.assertEqual(result.search_query, "背调 流程")

    async def test_http_returns_success_on_noop_and_model_failure(self):
        settings = Settings(auth_enabled=False, retrieval_mode="vector", query_rewrite_enabled=False)
        app = create_app(settings)
        isolated_rates(app, self)
        kb = knowledge_base("背调", "流程")
        session = Mock(begin=nullcontext, scalar=AsyncMock(return_value=False))
        store = Mock(similarity_search=AsyncMock(return_value=[]))
        store.name = "pgvector"
        app.dependency_overrides.update({
            get_session: lambda: session,
            get_llm: lambda: None,
            get_embedding_provider: lambda: Mock(embed=AsyncMock(return_value=[[1.0]])),
            get_vector_store: lambda: store,
            get_keyword_provider: lambda: Mock(),
            get_rerank_provider: lambda: NoopRerankProvider(),
        })
        body = {"profile": "custom", "kb_id": str(kb.id), "user_id": "tester", "query": "其他问题"}
        with (
            patch("app.repositories.catalog.get_knowledge_bases", new_callable=AsyncMock, return_value={kb.id: kb}),
            patch("app.repositories.catalog.add_retrieval_log", new_callable=AsyncMock),
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                noop = await client.post("/api/v1/rag/retrieve", json=body)
                self.assertIsNone(noop.raise_for_status().json()["data"]["metadata"]["query_processing"])
                failed = await client.post("/api/v1/rag/retrieve", json=body | {
                    "query": "背调", "query_options": {"enabled": True, "strategy": "rewrite"},
                })
                response = failed.raise_for_status().json()
                self.assertEqual(response["code"], 0)
                processing = response["data"]["metadata"]["query_processing"]
                self.assertTrue(processing["degraded"])
                self.assertEqual(processing["effective_query"], "背调")
                self.assertEqual(processing["search_query"], "背调 流程")

    async def test_retrieve_keeps_original_for_rerank_log_and_response(self):
        kb = knowledge_base("背调", "流程")
        session = Mock(begin=nullcontext, scalar=AsyncMock(return_value=False))
        embedding = Mock(embed=AsyncMock(return_value=[[1.0]]))
        store = Mock(similarity_search=AsyncMock(return_value=[]))
        store.name = "pgvector"
        keyword = Mock(keyword_search=AsyncMock(return_value=[]))
        keyword.name = "elasticsearch"
        reranker = Mock(rerank=AsyncMock(return_value=[]))
        reranker.name = "llm"
        settings = Settings(retrieval_mode="hybrid", query_rewrite_enabled=False, rerank_enabled=True)
        with (
            patch("app.repositories.catalog.get_knowledge_bases", new_callable=AsyncMock, return_value={kb.id: kb}) as get_kb,
            patch("app.repositories.catalog.add_retrieval_log", new_callable=AsyncMock) as log,
            patch("app.services.hybrid_search_service.resolve_keyword_matches", new_callable=AsyncMock, return_value=[]),
        ):
            for raw, search in [("背调", "背调 流程"), ("无匹配", "无匹配")]:
                command = RetrievalInput(profile="custom", kb_id=kb.id, tenant_id=kb.tenant_id, user_id="tester", query=raw)
                result = await retrieve(session, command, embedding, store, reranker, settings, keyword,
                                        plan=PLAN_PRESETS["pro"], rates=Mock(retrieval=lambda *_: nullcontext()))
                self.assertEqual(result.query, raw)
                self.assertEqual(log.await_args.args[1].query, raw)
                self.assertEqual(reranker.rerank.await_args.kwargs["query"], raw)
                embedding.embed.assert_awaited_with([search])
                self.assertEqual(keyword.keyword_search.await_args.kwargs["query"], search)
                processing = result.model_dump(exclude_none=True)["metadata"]["query_processing"]
                if raw == search:
                    self.assertIsNone(processing)
                else:
                    self.assertEqual(processing["search_query"], search)
            self.assertEqual(get_kb.await_count, 2)
