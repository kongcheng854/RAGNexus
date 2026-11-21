import json
import unittest
from uuid import uuid4

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.providers.llm.base import LLMProvider, LLMProviderError
from app.providers.llm.openai_compatible import OpenAICompatibleLLMProvider
from app.providers.rerank.llm import LLMRerankProvider
from app.providers.rerank.noop import NoopRerankProvider
from app.schemas.rag import RetrievalRequest
from app.schemas.rerank import RerankCandidate, RerankOptions
from app.services.rag_service import rank_results


def candidate(score: float) -> RerankCandidate:
    return RerankCandidate(
        document_id=uuid4(), chunk_id=uuid4(), title="退款规则",
        content="用户可在订单完成后 7 天内申请退款。", score=score,
    )


def completion(content: str, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
    })


class StaticLLM(LLMProvider):
    def __init__(self, rankings):
        self.rankings = rankings
        self.payload = None

    async def chat_json(self, *, system_prompt, user_payload, temperature=0.0, timeout_seconds=None):
        self.payload = user_payload
        return {"rankings": self.rankings}


class ChatProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_structured_messages_and_decodes_json(self):
        def respond(request):
            self.assertEqual(str(request.url), "https://example.test/v1/chat/completions")
            self.assertEqual(request.extensions["timeout"]["read"], 17)
            body = json.loads(request.content)
            self.assertEqual(body["model"], "test-model")
            self.assertEqual(body["response_format"], {"type": "json_object"})
            self.assertEqual(body["temperature"], 0.1)
            self.assertEqual(body["messages"][0], {"role": "system", "content": "Output JSON."})
            self.assertEqual(body["messages"][1]["role"], "user")
            self.assertEqual(json.loads(body["messages"][1]["content"]), {"query": "退款"})
            return completion('{"rankings": []}')

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url="https://example.test/v1"
        ) as client:
            result = await OpenAICompatibleLLMProvider(client, "test-model").chat_json(
                system_prompt="Output JSON.", user_payload={"query": "退款"},
                temperature=0.1, timeout_seconds=17,
            )
        self.assertEqual(result, {"rankings": []})

    async def test_rejects_invalid_chat_results_and_maps_upstream_failures(self):
        def respond(_request):
            if isinstance(outcome, httpx.HTTPError):
                raise outcome
            return outcome

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url="https://example.test/v1"
        ) as client:
            provider = OpenAICompatibleLLMProvider(client, "test-model")
            for outcome, error in (
                (completion("not JSON"), ErrorCode.LLM_NO_RESPONSE),
                (completion("[]"), ErrorCode.LLM_NO_RESPONSE),
                (completion('{"score": NaN}'), ErrorCode.LLM_NO_RESPONSE),
                (completion("{}", "length"), ErrorCode.LLM_NO_RESPONSE),
                (completion("{}", "content_filter"), ErrorCode.LLM_CONTENT_VIOLATION),
                (httpx.Response(429), ErrorCode.LLM_RATE_LIMIT),
                (httpx.ReadTimeout("timeout"), ErrorCode.LLM_TIMEOUT),
            ):
                with self.subTest(error=error):
                    with self.assertRaises(LLMProviderError) as failure:
                        await provider.chat_json(system_prompt="Output JSON.", user_payload={})
                    self.assertEqual(failure.exception.error, error)


class RerankTests(unittest.IsolatedAsyncioTestCase):
    async def test_limits_prompt_and_preserves_vector_scores_and_ties(self):
        first, second, third, excluded = [candidate(score) for score in (0.9, 0.8, 0.7, 0.6)]
        llm = StaticLLM([
            {"chunk_id": str(second.chunk_id), "rerank_score": 0.95, "reason": "internal"},
            {"chunk_id": str(second.chunk_id), "rerank_score": 0.05},
            {"chunk_id": str(uuid4()), "rerank_score": 10},
            {"chunk_id": str(third.chunk_id), "rerank_score": 0},
        ])
        provider = LLMRerankProvider(llm, chunk_max_chars=8, temperature=0.0)
        settings = Settings(rerank_enabled=False, rerank_max_candidates=3)
        request = RetrievalRequest(
            tenant_id="tenant", kb_id=uuid4(), user_id="user", query="申请时限",
            rerank_options=RerankOptions(enabled=True, top_n=3),
        )
        results, metadata = await rank_results(
            request, [first, second, third, excluded], provider, settings, 4, uuid4()
        )

        self.assertEqual([result.chunk_id for result in results], [
            second.chunk_id, first.chunk_id, third.chunk_id,
        ])
        self.assertEqual([result.rerank_score for result in results], [0.95, 0.0, 0.0])
        self.assertEqual([result.score for result in results], [0.8, 0.9, 0.7])
        self.assertEqual(results[0].content, second.content)
        self.assertNotIn("reason", results[0].model_dump())
        self.assertEqual(llm.payload["query"], "申请时限")
        self.assertEqual(llm.payload["top_n"], 3)
        self.assertEqual(llm.payload["candidates"], [
            {
                "chunk_id": str(chunk.chunk_id), "document_id": str(chunk.document_id),
                "title": chunk.title, "content": chunk.content[:8], "vector_score": chunk.score,
            }
            for chunk in (first, second, third)
        ])
        self.assertTrue(metadata.enabled)
        self.assertEqual(metadata.candidate_count, 3)

    async def test_bad_scores_degrade_and_noop_returns_the_vector_limit(self):
        first, second = candidate(0.9), candidate(0.8)
        request = RetrievalRequest(tenant_id="tenant", kb_id=uuid4(), user_id="user", query="退款")
        settings = Settings(rerank_enabled=True, rerank_top_n=1)
        for score in (-0.1, 1.1, "0.9", True):
            with self.subTest(score=score):
                llm = StaticLLM([{"chunk_id": str(second.chunk_id), "rerank_score": score}])
                provider = LLMRerankProvider(llm, chunk_max_chars=10, temperature=0.0)
                with self.assertLogs("app.services.rag_service", level="WARNING"):
                    results, metadata = await rank_results(
                        request, [first, second], provider, settings, 2, uuid4()
                    )
                self.assertTrue(metadata.degraded)
                self.assertEqual(results[0].chunk_id, first.chunk_id)
                self.assertIsNone(results[0].rerank_score)

        results, metadata = await rank_results(
            request, [first, second], NoopRerankProvider(), settings, 2, uuid4(),
        )
        self.assertFalse(metadata.enabled)
        self.assertEqual(len(results), 2)

    def test_counts_reject_unsupported_values(self):
        for invalid in (0, -1, True, "20", 1001):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                RetrievalRequest(
                    tenant_id="tenant", kb_id=uuid4(), user_id="user", query="退款", top_k=invalid
                )
        with self.assertRaises(ValidationError):
            RerankOptions(top_n=0)
