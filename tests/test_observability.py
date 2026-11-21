import asyncio
import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

import httpx
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, OperationalError, TimeoutError

from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import EmbeddingError
from app.core.logging import AppLogging, log_event
from app.main import create_app
from app.providers.embedding.openai_compatible import OpenAICompatibleEmbeddingProvider


class ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.log_dir = Path(directory.name)

    async def test_request_id_payload_redaction_and_private_tracebacks(self):
        app = create_app(Settings(log_dir=self.log_dir, log_payloads=True, log_payload_limit=256))

        @app.post("/log-test")
        async def echo(body: dict) -> dict:
            await asyncio.sleep(0)
            return body

        @app.get("/log-failure")
        async def explode():
            raise RuntimeError("private implementation detail")

        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response, failure = await asyncio.gather(
                    client.post(
                        "/log-test?token=query-secret",
                        json={"example": "ok", "nested": {"api-key": "body-secret"}},
                    ),
                    client.get("/log-failure"),
                )
                self.assertEqual(response.json()["nested"]["api-key"], "body-secret")
                self.assertEqual(failure.status_code, 500)
                self.assertEqual(failure.json()["code"], ErrorCode.SERVER_ERROR)
                self.assertNotIn("private", failure.text)
                self.assertNotEqual(response.headers["x-request-id"], failure.headers["x-request-id"])
                oversized = await client.post("/log-test", json={"example": "X" * 300})
                self.assertEqual(oversized.status_code, 200)
                method_error = await client.get("/log-test")
                self.assertEqual(method_error.json()["code"], ErrorCode.METHOD_ERROR)
                self.assertEqual(method_error.headers["allow"], "POST")

        normal = (self.log_dir / "app.log").read_text(encoding="utf-8")
        errors = (self.log_dir / "error.log").read_text(encoding="utf-8")
        for result in (response, failure):
            request_id = str(UUID(result.headers["x-request-id"]))
            for event in ("API_REQUEST", "API_RESPONSE"):
                self.assertRegex(normal, rf"request_id={request_id} \| {event} .*")
        self.assertIn("[redacted]", normal)
        self.assertIn("[body exceeds capture limit]", normal)
        self.assertNotIn("body-secret", normal)
        self.assertNotIn("query-secret", normal)
        self.assertNotIn("API_RESPONSE", errors)
        self.assertIn("Traceback", errors)
        self.assertIn("RuntimeError: private implementation detail", errors)
        self.assertIn(f'request_id={failure.headers["x-request-id"]}', errors)

    async def test_database_errors_map_to_safe_responses(self):
        app = create_app(Settings(log_dir=self.log_dir))

        class UniqueViolation(Exception):
            sqlstate = "23505"

        failures = {
            "duplicate": (IntegrityError(None, None, UniqueViolation("private value")), ErrorCode.DATA_DUPLICATE),
            "timeout": (TimeoutError("private value"), ErrorCode.DB_TIMEOUT),
            "offline": (OperationalError(None, None, ValueError("private value")), ErrorCode.DB_UNAVAILABLE),
            "http": (HTTPException(418, "private value"), ErrorCode.API_REQUEST_ERROR),
        }

        @app.get("/error-test/{key}")
        async def fail(key: str):
            raise failures[key][0]

        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                for key, (_, error) in failures.items():
                    with self.subTest(key=key):
                        response = await client.get(f"/error-test/{key}")
                        self.assertEqual(response.status_code, 418 if key == "http" else error.status)
                        self.assertEqual(response.json(), {"code": error, "msg": error.message, "data": None})
                        self.assertNotIn("private", response.text)

    async def test_both_files_rotate_and_receive_expected_levels(self):
        logs = AppLogging(
            directory=self.log_dir, level="INFO", max_bytes=512, backups=2,
            queue_size=128, color=False,
        )
        logger = logging.getLogger("app.tests")
        logs.start()
        try:
            for index in range(10):
                log_event(logger, logging.INFO, "GENERAL", index=index, text="a" * 50)
                log_event(logger, logging.ERROR, "FAILURE", index=index, text="b" * 50)
        finally:
            await logs.stop()

        for name in ("app.log", "error.log"):
            self.assertTrue((self.log_dir / f"{name}.1").exists())
            self.assertTrue((self.log_dir / f"{name}.2").exists())
            self.assertFalse((self.log_dir / f"{name}.3").exists())
        self.assertIn("GENERAL", (self.log_dir / "app.log").read_text())
        self.assertNotIn("GENERAL", (self.log_dir / "error.log").read_text())
        self.assertIn("FAILURE", (self.log_dir / "error.log").read_text())

    async def test_model_error_mapping_and_logging(self):
        outcomes = iter([
            httpx.ReadTimeout("provider timed out"),
            httpx.Response(429),
            httpx.Response(400, json={"error": {"code": "context_length_exceeded"}}),
            httpx.Response(422, json={"error": {"type": "content_policy_violation"}}),
            httpx.Response(404),
            httpx.Response(401),
            httpx.Response(200, json={"data": []}),
            httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.25, 0.75]}]}),
            httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.125, 0.875]}]}),
        ])

        def respond(_request):
            outcome = next(outcomes)
            if isinstance(outcome, httpx.HTTPError):
                raise outcome
            return outcome

        app = create_app(Settings(log_dir=self.log_dir))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(respond), base_url="https://example.test/v1"
            ) as client:
                provider = OpenAICompatibleEmbeddingProvider(client, "test-model")
                for expected in (
                    ErrorCode.LLM_TIMEOUT, ErrorCode.LLM_RATE_LIMIT, ErrorCode.LLM_TOKEN_LIMIT,
                    ErrorCode.LLM_CONTENT_VIOLATION, ErrorCode.LLM_MODEL_ERROR,
                    ErrorCode.LLM_AUTH_ERROR, ErrorCode.LLM_NO_RESPONSE,
                ):
                    with self.subTest(expected=expected):
                        with self.assertRaises(EmbeddingError) as raised:
                            await provider.embed(["private prompt"])
                        self.assertEqual(raised.exception.error, expected)
                self.assertEqual(await provider.embed(["private prompt"]), [[0.25, 0.75]])
                verbose = OpenAICompatibleEmbeddingProvider(client, "test-model", log_payloads=True)
                await verbose.embed(["visible model sample"])

        normal = (self.log_dir / "app.log").read_text(encoding="utf-8")
        errors = (self.log_dir / "error.log").read_text(encoding="utf-8")
        self.assertIn("MODEL_REQUEST", normal)
        self.assertIn("MODEL_RESPONSE", normal)
        self.assertNotIn("private prompt", normal)
        self.assertRegex(errors, r'MODEL_FAILURE .*"code": 40001')
        self.assertIn("test-model", normal)
        self.assertIn('"dimensions": 2', normal)
        self.assertIn("visible model sample", normal)
        self.assertIn("[[0.125, 0.875]]", normal)
