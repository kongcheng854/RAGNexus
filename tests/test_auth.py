import asyncio
import hashlib
import logging
import os
import re
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import httpx
from sqlalchemy import select, update

from app.api.dependencies import get_embedding_provider
from app.core.auth import generate_api_key, hash_api_key
from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.logging import EventFormatter
from app.main import create_app
from app.models import ApiKey, KnowledgeBase, RetrievalLog, Tenant
from app.providers.embedding.base import EmbeddingProvider
from scripts.create_tenant import main as tenant_cli
from scripts.create_api_key import main as key_cli


DATABASE_URL = os.getenv("RAGNEXUS_TEST_DATABASE_URL")
OPERATIONS = [
    ("GET", "/auth/me", None),
    ("GET", "/knowledge-bases/tree", None),
    ("POST", "/knowledge-bases/create", {"name": "test"}),
    ("POST", "/documents/upload", {"kb_id": str(uuid4()), "title": "test", "content": "text"}),
    ("POST", "/rag/retrieve", {"kb_id": str(uuid4()), "user_id": "user", "query": "text"}),
]


class AuthTests(unittest.IsolatedAsyncioTestCase):
    def test_key_format_hash_and_uniqueness(self):
        plaintext, digest, prefix = generate_api_key()
        self.assertRegex(plaintext, r"^rk_live_[0-9a-f]{32}$")
        self.assertEqual(digest, hashlib.sha256(plaintext.encode()).hexdigest())
        self.assertEqual(prefix, "rk_" + plaintext[8:16])
        self.assertNotEqual(plaintext, generate_api_key()[0])
        record = logging.LogRecord("app.auth", logging.INFO, __file__, 0, plaintext, (), None)
        record.fields = {"preview": plaintext, "key_prefix": prefix}
        rendered = EventFormatter().format(record)
        self.assertNotIn(plaintext, rendered)
        self.assertIn(prefix, rendered)

    async def test_missing_bearer_rejected_for_all_operations_without_database(self):
        with TemporaryDirectory() as directory:
            app = create_app(Settings(auth_enabled=True, log_dir=Path(directory)))
            schemas = app.openapi()["components"]["schemas"]
            for name in ("KnowledgeBaseCreate", "DocumentUpload", "RetrievalRequest"):
                self.assertNotIn("tenant_id", schemas[name]["properties"])
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1",
                ) as client:
                    for method, path, body in OPERATIONS:
                        for header in ({}, {"Authorization": "Basic invalid"}, {"Authorization": "Bearer invalid"}):
                            response = await client.request(method, path, json=body, headers=header)
                            self.assertEqual(response.status_code, 401, response.text)
                            self.assertEqual(response.json()["code"], 20010)
                            self.assertEqual(response.headers["www-authenticate"], "Bearer")

    async def test_disabled_auth_uses_fixed_placeholder_tenant(self):
        with TemporaryDirectory() as directory:
            app = create_app(Settings(auth_enabled=False, log_dir=Path(directory)))
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1",
                ) as client:
                    response = await client.get("/auth/me", headers={"Authorization": "Bearer invalid"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["data"], {
                        "tenant_id": "tenant_demo", "tenant_name": "演示租户",
                        "key_prefix": None, "key_name": None,
                    })


class FixedEmbedding(EmbeddingProvider):
    async def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


@unittest.skipUnless(DATABASE_URL, "set RAGNEXUS_TEST_DATABASE_URL")
class AuthDatabaseTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform == "win32":
            cls.addClassCleanup(asyncio.set_event_loop_policy, asyncio.get_event_loop_policy())
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    async def test_tenant_isolation_lifecycle_ignored_body_and_disabled_auth(self):
        with TemporaryDirectory() as directory:
            app = create_app(Settings(auth_enabled=True, database_url=DATABASE_URL, log_dir=Path(directory), log_payloads=True))
            app.dependency_overrides[get_embedding_provider] = FixedEmbedding
            a, b = "auth-a-" + uuid4().hex, "auth-b-" + uuid4().hex
            key_a, hash_a, prefix_a = generate_api_key()
            key_b, hash_b, prefix_b = generate_api_key()
            key_id_a, key_id_b = uuid4(), uuid4()
            async with app.router.lifespan_context(app):
                async with app.state.session_factory() as session:
                    async with session.begin():
                        session.add_all([Tenant(id=a, name="租户 A"), Tenant(id=b, name="租户 B")])
                        await session.flush()
                        session.add_all([
                            ApiKey(id=key_id_a, tenant_id=a, key_hash=hash_a, key_prefix=prefix_a, name="A"),
                            ApiKey(id=key_id_b, tenant_id=b, key_hash=hash_b, key_prefix=prefix_b, name="B"),
                        ])
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1",
                ) as client:
                    headers_a, headers_b = {"Authorization": "Bearer " + key_a}, {"Authorization": "Bearer " + key_b}
                    me = await client.get("/auth/me", headers=headers_a)
                    self.assertEqual(me.json()["data"]["tenant_id"], a)
                    self.assertEqual(me.json()["data"]["key_name"], "A")
                    created = await client.post("/knowledge-bases/create", headers=headers_a, json={"name": "alpha library", "tenant_id": b})
                    kb_a = created.raise_for_status().json()["data"]["kb_id"]
                    self.assertEqual(created.json()["data"]["tenant_id"], a)
                    created_b = await client.post("/knowledge-bases/create", headers=headers_b, json={"name": "beta library"})
                    kb_b = created_b.raise_for_status().json()["data"]["kb_id"]
                    uploaded = await client.post("/documents/upload", headers=headers_a, json={
                        "kb_id": kb_a, "title": "a.txt", "content": "private A", "tenant_id": b,
                    })
                    uploaded.raise_for_status()
                    for headers, own_kb, own_tenant in ((headers_a, kb_a, a), (headers_b, kb_b, b)):
                        tree = await client.get("/knowledge-bases/tree", headers=headers)
                        data = tree.raise_for_status().json()["data"]
                        self.assertEqual([t["tenant_id"] for t in data], [own_tenant])
                        self.assertEqual([kb["kb_id"] for kb in data[0]["knowledge_bases"]], [own_kb])
                    filtered = await client.get("/knowledge-bases/tree", headers=headers_a, params={"keyword": b})
                    self.assertEqual(filtered.json()["data"], [])
                    named = await client.get("/knowledge-bases/tree", headers=headers_a, params={"keyword": "ALPHA"})
                    self.assertEqual(named.json()["data"][0]["knowledge_bases"][0]["kb_id"], kb_a)

                    request = {"kb_id": kb_a, "user_id": "user", "query": "private", "tenant_id": a}
                    denied = await client.post("/rag/retrieve", headers=headers_b, json=request)
                    self.assertEqual(denied.status_code, 404)
                    self.assertEqual(denied.json()["code"], ErrorCode.KNOWLEDGE_BASE_NOT_FOUND)
                    denied_upload = await client.post("/documents/upload", headers=headers_b, json={
                        "tenant_id": a, "kb_id": kb_a, "title": "spoof", "content": "spoof",
                    })
                    self.assertEqual(denied_upload.status_code, 404)
                    retrieved = await client.post("/rag/retrieve", headers=headers_a, json=request | {"tenant_id": b})
                    retrieved.raise_for_status()
                    async with app.state.session_factory() as session:
                        log = await session.scalar(select(RetrievalLog).where(RetrievalLog.tenant_id == a))
                        self.assertIsNotNone(log)

                    async def change_key(**values):
                        async with app.state.session_factory() as session, session.begin():
                            await session.execute(update(ApiKey).where(ApiKey.id == key_id_a).values(**values))

                    for values in (
                        {"status": "revoked"},
                        {"status": "active", "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
                    ):
                        await change_key(**values)
                        invalid = await client.get("/auth/me", headers=headers_a)
                        self.assertEqual(invalid.status_code, 401)
                        self.assertEqual(invalid.json()["code"], 20010)
                    await change_key(status="active", expires_at=None)
                    await change_key(expires_at=datetime.now(timezone.utc) + timedelta(days=1))
                    self.assertEqual((await client.get("/auth/me", headers=headers_a)).status_code, 200)
                    async with app.state.session_factory() as session, session.begin():
                        await session.execute(update(Tenant).where(Tenant.id == a).values(status="disabled"))
                    self.assertEqual((await client.get("/auth/me", headers=headers_a)).status_code, 401)
                    self.assertEqual((await client.get("/auth/me", headers=headers_b)).status_code, 200)
                    random_key = "rk_live_" + "a" * 32
                    self.assertEqual((await client.get("/auth/me", headers={"Authorization": "Bearer " + random_key})).status_code, 401)
                    app.state.settings.auth_enabled = False
                    fallback = await client.post("/knowledge-bases/create", json={"name": "fallback", "tenant_id": b})
                    self.assertEqual(fallback.raise_for_status().json()["data"]["tenant_id"], "tenant_demo")
            logs = (Path(directory) / "app.log").read_text(encoding="utf8")
            self.assertNotIn(key_a, logs)
            self.assertNotIn(key_b, logs)
            self.assertIn(prefix_a, logs)

    async def test_operator_scripts_print_key_once_and_store_hash_only(self):
        tenant_id = "cli-" + uuid4().hex
        def run_cli(function, args):
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr), patch.object(sys, "argv", args), patch.dict(
                os.environ, {"DATABASE_URL": DATABASE_URL},
            ):
                status = function()
            return status, stdout.getvalue(), stderr.getvalue()

        status, output, _ = await asyncio.to_thread(
            run_cli, tenant_cli, ["create_tenant.py", "--id", tenant_id, "--name", "CLI tenant"],
        )
        self.assertEqual(status, 0)
        duplicate, _, _ = await asyncio.to_thread(
            run_cli, tenant_cli, ["create_tenant.py", "--id", tenant_id, "--name", "duplicate"],
        )
        self.assertEqual(duplicate, 1)
        status, output, _ = await asyncio.to_thread(
            run_cli, key_cli, ["create_api_key.py", "--tenant-id", tenant_id, "--name", "CLI key"],
        )
        self.assertEqual(status, 0)
        plaintext = output.strip()
        self.assertRegex(plaintext, r"^rk_live_[0-9a-f]{32}$")
        self.assertEqual(output.count(plaintext), 1)
        with TemporaryDirectory() as directory:
            app = create_app(Settings(auth_enabled=True, database_url=DATABASE_URL, log_dir=Path(directory)))
            async with app.router.lifespan_context(app):
                async with app.state.session_factory() as session:
                    key = await session.scalar(select(ApiKey).where(ApiKey.tenant_id == tenant_id))
                    self.assertEqual(key.key_hash, hash_api_key(plaintext))
                    self.assertNotIn(plaintext, str(key.__dict__))
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1",
                ) as client:
                    response = await client.get("/auth/me", headers={"Authorization": "Bearer " + plaintext})
                    self.assertEqual(response.status_code, 200)
        absent, _, _ = await asyncio.to_thread(
            run_cli, key_cli, ["create_api_key.py", "--tenant-id", "missing-" + uuid4().hex, "--name", "invalid"],
        )
        self.assertEqual(absent, 1)
