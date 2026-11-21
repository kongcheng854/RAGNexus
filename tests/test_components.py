import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import EmbeddingError
from app.main import create_app
from app.providers.embedding.openai_compatible import OpenAICompatibleEmbeddingProvider
from app.utils.text_splitter import CharacterTextSplitter


class SplitterTests(unittest.TestCase):
    def test_overlaps_without_emitting_a_redundant_tail(self):
        self.assertEqual(CharacterTextSplitter(4, 2).split("甲乙丙丁戊己"), ["甲乙丙丁", "丙丁戊己"])


class EmbeddingTests(unittest.IsolatedAsyncioTestCase):
    async def test_reorders_batched_response(self):
        def respond(request):
            self.assertEqual(request.url.path, "/v1/embeddings")
            self.assertEqual(json.loads(request.content), {"model": "demo", "input": ["a", "b"]})
            return httpx.Response(200, json={"data": [
                {"index": 1, "embedding": [0, 1]},
                {"index": 0, "embedding": [1, 0]},
            ]})

        async with httpx.AsyncClient(
            base_url="https://example.test/v1", transport=httpx.MockTransport(respond)
        ) as client:
            provider = OpenAICompatibleEmbeddingProvider(client, "demo")
            self.assertEqual(await provider.embed(["a", "b"]), [[1.0, 0.0], [0.0, 1.0]])

    async def test_rejects_wrong_response_index(self):
        def respond(_request):
            return httpx.Response(200, json={"data": [{"index": 1, "embedding": [1]}]})

        async with httpx.AsyncClient(
            base_url="https://example.test/v1", transport=httpx.MockTransport(respond)
        ) as client:
            with self.assertRaises(EmbeddingError):
                await OpenAICompatibleEmbeddingProvider(client, "demo").embed(["a"])


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_core_operations_and_uniform_validation_errors(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app = create_app(Settings(log_dir=Path(directory.name), auth_enabled=False))
        self.assertEqual(set(app.openapi()["paths"]), {
            "/api/v1/knowledge-bases/create",
            "/api/v1/knowledge-bases/tree",
            "/api/v1/auth/me",
            "/api/v1/documents/upload",
            "/api/v1/rag/retrieve",
        })
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                invalid = await client.post("/api/v1/documents/upload", json={"kb_id": "invalid"})
                self.assertEqual(invalid.status_code, 422)
                self.assertEqual(invalid.json()["code"], ErrorCode.PARAM_ERROR)
                self.assertTrue(invalid.json()["data"]["errors"])
                self.assertEqual((await client.get("/docs")).json()["code"], ErrorCode.NOT_FOUND)
