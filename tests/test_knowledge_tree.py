import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx

from app.core.config import Settings
from app.core.auth import TenantContext
from app.api.v1.deps import get_current_tenant
from app.main import create_app
from app.models import Chunk, Document, IndexStatus, KnowledgeBase


DATABASE_URL = os.getenv("RAGNEXUS_TEST_DATABASE_URL")


@unittest.skipUnless(DATABASE_URL, "set RAGNEXUS_TEST_DATABASE_URL")
class KnowledgeTreeTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform == "win32":
            cls.addClassCleanup(asyncio.set_event_loop_policy, asyncio.get_event_loop_policy())
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    async def test_tenant_filter_order_status_and_chunk_counts(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app = create_app(Settings(database_url=DATABASE_URL, log_dir=Path(directory.name)))
        prefix = "tree-" + uuid4().hex
        app.dependency_overrides[get_current_tenant] = lambda: TenantContext(prefix + "_tech", "test")
        now = datetime.now(timezone.utc)
        older = now - timedelta(days=1)
        old_kb, new_kb, other_kb = uuid4(), uuid4(), uuid4()
        old_doc, new_doc, failed_doc, processing_doc = [uuid4() for _ in range(4)]
        async with app.router.lifespan_context(app):
            async with app.state.session_factory() as session:
                async with session.begin():
                    session.add_all([
                        KnowledgeBase(id=old_kb, tenant_id=prefix + "_tech", name="Tech%旧知识库", description=None, created_at=older),
                        KnowledgeBase(id=new_kb, tenant_id=prefix + "_tech", name="tech_new", description="空库", created_at=now),
                        KnowledgeBase(id=other_kb, tenant_id=prefix + "_other%", name="其他租户", description=None),
                    ])
                    await session.flush()
                    session.add_all([
                        Document(id=old_doc, tenant_id=prefix + "_tech", kb_id=old_kb, title="old.md", source_type="text", status=1, created_at=older),
                        Document(id=new_doc, tenant_id=prefix + "_tech", kb_id=old_kb, title="new.md", source_type="text", status=1, created_at=now),
                        Document(id=failed_doc, tenant_id=prefix + "_tech", kb_id=old_kb, title="failed.md", source_type="text", status=2),
                        Document(id=processing_doc, tenant_id=prefix + "_tech", kb_id=old_kb, title="processing.md", source_type="text", status=3),
                    ])
                    await session.flush()
                    session.add_all([
                        Chunk(
                            id=uuid4(), tenant_id=prefix + "_tech", kb_id=old_kb,
                            document_id=old_doc, title="old.md", content="片段",
                            chunk_metadata={}, embedding=[1.0, 0.0],
                        ) for _ in range(2)
                    ])

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1/",
            ) as client:
                response = await client.get("knowledge-bases/tree", params={"keyword": "TECH"})
                tenants = response.raise_for_status().json()["data"]
                self.assertEqual([t["tenant_id"] for t in tenants], [prefix + "_tech"])
                self.assertEqual(len(tenants[0]["knowledge_bases"]), 2)
                bases = tenants[0]["knowledge_bases"]
                self.assertEqual([kb["kb_id"] for kb in bases], [str(new_kb), str(old_kb)])
                self.assertEqual(bases[0]["documents"], [])
                docs = {doc["document_id"]: doc for doc in bases[1]["documents"]}
                self.assertEqual(docs[str(failed_doc)]["status"], "FAILED")
                self.assertEqual(docs[str(processing_doc)]["status"], "PROCESSING")
                success = [doc for doc in bases[1]["documents"] if doc["status"] == "SUCCESS"]
                self.assertEqual([doc["document_id"] for doc in success], [str(new_doc), str(old_doc)])
                self.assertEqual([doc["chunk_count"] for doc in success], [0, 2])
                filtered = await client.get("knowledge-bases/tree", params={"keyword": "旧知识库"})
                self.assertEqual(len(filtered.json()["data"]), 1)
                literal = await client.get("knowledge-bases/tree", params={"keyword": "%"})
                self.assertEqual([kb["kb_id"] for kb in literal.json()["data"][0]["knowledge_bases"]], [str(old_kb)])
                missing = await client.get("knowledge-bases/tree", params={"keyword": uuid4().hex})
                self.assertEqual(missing.json(), {"code": 0, "msg": "success", "data": []})
