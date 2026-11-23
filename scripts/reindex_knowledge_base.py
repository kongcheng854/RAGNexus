"""Rebuild a knowledge base from its saved documents with the indexing worker."""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

import httpx
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import Settings
from app.core.error_codes import ErrorCode
from app.core.exceptions import ServiceError
from app.db.session import standalone_session
from app.models import Document, IndexStatus, KnowledgeBase, Tenant
from app.providers.keyword_search.base import KeywordSearchProvider
from app.providers.keyword_search.elasticsearch import ElasticsearchKeywordSearchProvider
from app.services.document_service import reindex_document
from app.tenant.plan_presets import PLAN_PRESETS, PlanLimits


async def rebuild_one(
    session: AsyncSession, keyword: KeywordSearchProvider, tenant: str, kb_id: UUID, document_id: UUID,
    updated_at: datetime, limits: PlanLimits, timeout: int, reparse: bool,
) -> None:
    clock = asyncio.get_running_loop().time
    deadline = clock() + timeout
    while True:
        try:
            await reindex_document(session, keyword, tenant, document_id, limits,
                                   kb_id=kb_id, expected_updated_at=updated_at, reparse=reparse)
            break
        except ServiceError as exc:
            if exc.error != ErrorCode.QUOTA_EXCEEDED:
                raise
            if clock() >= deadline:
                raise TimeoutError("等待租户并发索引名额超时") from None
            await asyncio.sleep(1)
    print(f"  {document_id} 已投递，等待 Worker", flush=True)
    deadline = clock() + timeout
    while True:
        async with session.begin():
            result = (await session.execute(select(Document.status, Document.error_message).where(
                Document.id == document_id, Document.tenant_id == tenant, Document.kb_id == kb_id,
            ))).one_or_none()
        if result is None:
            raise ValueError("文档已删除")
        if result.status == IndexStatus.SUCCESS:
            return
        if result.status == IndexStatus.FAILED:
            raise ValueError(result.error_message or "Worker 索引失败")
        if clock() >= deadline:
            raise TimeoutError("等待 Worker 超时，已投递的任务仍可继续，请检查文档状态")
        await asyncio.sleep(1)


async def rebuild(
    session: AsyncSession, keyword: KeywordSearchProvider, kb_id: UUID, tenant_id: str | None,
    document_ids: list[UUID], timeout: int, *, auth_enabled: bool, reparse: bool = False,
) -> dict[str, int]:
    async with session.begin():
        scope = select(KnowledgeBase.tenant_id, Tenant.plan).outerjoin(
            Tenant, Tenant.id == KnowledgeBase.tenant_id
        ).where(KnowledgeBase.id == kb_id)
        if tenant_id is not None:
            scope = scope.where(KnowledgeBase.tenant_id == tenant_id)
        kb = (await session.execute(scope)).one_or_none()
        if kb is None:
            raise ValueError("知识库不存在或与指定租户不匹配")
        tenant, plan = kb
        if tenant == "tenant_demo" and not auth_enabled:
            plan = "pro"
        limits = PLAN_PRESETS[plan or "free"].limits
        query = select(Document.id, Document.status, Document.updated_at).where(
            Document.kb_id == kb_id, Document.tenant_id == tenant,
        )
        if document_ids:
            query = query.where(Document.id.in_(document_ids))
        docs = {row.id: row for row in await session.execute(query.order_by(Document.id))}

    selected = list(dict.fromkeys(document_ids)) if document_ids else list(docs)
    counts = {"success": 0, "failed": 0, "skipped": 0}
    print(f"kb_id={kb_id} tenant_id={tenant} 共 {len(selected)} 篇", flush=True)
    for i, identifier in enumerate(selected, 1):
        doc = docs.get(identifier)
        prefix = f"[{i}/{len(selected)}] {identifier}"
        print(prefix, flush=True)
        if doc is None:
            state, reason = "failed", "目标知识库内未找到文档"
        elif doc.status == IndexStatus.PROCESSING or (not document_ids and doc.status != IndexStatus.SUCCESS):
            state, reason = "skipped", IndexStatus(doc.status).name
        else:
            try:
                await rebuild_one(session, keyword, tenant, kb_id, doc.id, doc.updated_at, limits, timeout, reparse)
                state, reason = "success", "索引完成"
            except ServiceError as exc:
                state = "skipped" if exc.error == ErrorCode.DATA_CONFLICT else "failed"
                reason = str(exc)
            except (TimeoutError, ValueError) as exc:
                state, reason = "failed", str(exc)
            except Exception as exc:
                state, reason = "failed", type(exc).__name__
        counts[state] += 1
        print(f"{prefix} {state} · {reason}", flush=True)
    print(" ".join(f"{name}={count}" for name, count in counts.items()), flush=True)
    return counts


async def run(args) -> int:
    settings = Settings()
    async with standalone_session() as session, httpx.AsyncClient(
        base_url=str(settings.elasticsearch_url), timeout=settings.elasticsearch_timeout_seconds,
    ) as es:
        counts = await rebuild(session, ElasticsearchKeywordSearchProvider(es, settings.elasticsearch_index),
                               args.kb_id, args.tenant_id, args.document_id, args.wait_timeout,
                               auth_enabled=settings.auth_enabled, reparse=args.reparse)
    return int(counts["failed"] > 0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="从保存的原文逐篇重建知识库索引")
    parser.add_argument("--kb-id", type=UUID, required=True)
    parser.add_argument("--tenant-id", help="校验知识库所属租户，省略时从数据库读取")
    parser.add_argument("--document-id", type=UUID, action="append", default=[], help="限指定文档，可重复提供")
    parser.add_argument("--reparse", action="store_true", help="从已保存的源文件重新解析后建索引")
    parser.add_argument("--wait-timeout", type=int, default=600, help="等待名额或单篇任务完成的最长秒数，默认 600")
    args = parser.parse_args(argv)
    if args.wait_timeout < 1:
        parser.error("--wait-timeout 需为正整数")
    try:
        factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=factory) as runner:
            return runner.run(run(args))
    except ValidationError:
        print("配置无效，请检查项目 .env 的字段格式。", file=sys.stderr)
    except SQLAlchemyError:
        print("读取知识库失败，请检查数据库连接和迁移版本。", file=sys.stderr)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
