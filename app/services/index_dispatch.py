"""Publish committed indexing requests and recover requests interrupted before publication."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.error_codes import ErrorCode
from app.core.exceptions import ServiceError
from app.models import Document, IndexStatus
from app.tasks.indexing import index_document_task


logger = logging.getLogger(__name__)


async def dispatch_document(session: AsyncSession, document_id: UUID) -> None:
    failure = None
    async with session.begin():
        # The worker locks the same row. After an ambiguous publish/crash a duplicate is harmless.
        doc = await session.scalar(select(Document).where(
            Document.id == document_id, Document.dispatch_pending.is_(True),
        ).with_for_update())
        if doc is None:
            return
        if doc.status == IndexStatus.PROCESSING:
            try:
                await asyncio.to_thread(index_document_task.delay, str(document_id))
            except Exception as exc:
                failure = exc
                doc.status = IndexStatus.FAILED
                doc.error_message = "索引任务投递失败"
        doc.dispatch_pending = False
    if failure:
        raise ServiceError(ErrorCode.SYSTEM_BUSY, data={"document_id": str(document_id)}) from failure


async def recover_pending(sessions: async_sessionmaker[AsyncSession]) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=30)
    async with sessions() as session:
        async with session.begin():
            ids = (await session.scalars(select(Document.id).where(
                Document.dispatch_pending.is_(True), Document.updated_at < cutoff,
            ).order_by(Document.updated_at).limit(100))).all()
        for identifier in ids:
            try:
                await dispatch_document(session, identifier)
            except ServiceError:
                logger.warning("Index dispatch failed for document %s", identifier)


async def recover_dispatches(sessions: async_sessionmaker[AsyncSession]) -> None:
    while True:
        await asyncio.sleep(30)
        try:
            await recover_pending(sessions)
        except Exception:
            logger.exception("Could not recover pending index dispatches")
