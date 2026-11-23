import json
import unittest
from unittest.mock import patch

import httpx

from app.core.config import Settings
from app.observability.langfuse_client import RetrieveObservability, langfuse_connection


class LangfuseDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = Settings(_env_file=None, langfuse_enabled=True,
                                 langfuse_public_key="test-public", langfuse_secret_key="test-secret")

    def test_client_initialization_failure_leaves_application_available(self):
        http = httpx.Client(trust_env=False)
        with patch("app.observability.langfuse_client.httpx.Client", return_value=http), \
             patch("app.observability.langfuse_client.FernLangfuse", side_effect=ValueError("setup failed")), \
             self.assertLogs("app.observability.langfuse_client", level="WARNING"), \
             langfuse_connection(self.settings) as client:
            self.assertIsNone(client)
        self.assertTrue(http.is_closed)

    async def test_partial_batch_feedback_requires_acknowledged_root(self):
        seen = []

        def respond(request):
            events = json.loads(request.content)["batch"]
            seen.extend(events)
            successes, errors = [], []
            for event in events:
                if event["body"]["name"] == rejected:
                    errors.append({"id": event["id"], "status": 400, "message": "invalid event"})
                else:
                    successes.append({"id": event["id"], "status": 201})
            return httpx.Response(207, json={"successes": successes, "errors": errors})

        http = httpx.Client(transport=httpx.MockTransport(respond))
        with patch("app.observability.langfuse_client.httpx.Client", return_value=http), \
             langfuse_connection(self.settings) as client:
            for rejected in ("rerank", "rag_retrieve"):
                with self.subTest(rejected=rejected):
                    seen.clear()
                    obs = RetrieveObservability(client, query="question", metadata={"user_id": "tester"})
                    with obs.span("rerank") as summary:
                        summary.update(enabled=True, degraded=True)
                    with self.assertLogs("app.observability.langfuse_client", level="WARNING"):
                        trace_id = await obs.flush()
                    self.assertEqual(seen[1]["body"]["level"], "WARNING")
                    self.assertEqual(trace_id, obs.trace_id if rejected == "rerank" else None)
