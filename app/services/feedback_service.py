import asyncio
import logging
from uuid import UUID, uuid5

from langfuse.api import CreateScoreRequest
from langfuse.api.client import FernLangfuse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.models import RetrievalLog
from app.schemas.feedback import FeedbackData, FeedbackRequest


logger = logging.getLogger(__name__)


async def submit_feedback(
    session: AsyncSession, tenant_id: str, feedback: FeedbackRequest, client: FernLangfuse | None,
    *, submission_id: UUID | None = None,
) -> FeedbackData:
    if client is None:
        raise AppError(ErrorCode.FEEDBACK_UNAVAILABLE)
    lookup = select(RetrievalLog.id).where(
        RetrievalLog.tenant_id == tenant_id, RetrievalLog.trace_id == feedback.trace_id,
    )
    if feedback.log_id is not None:
        lookup = lookup.where(RetrievalLog.id == feedback.log_id)
    async with session.begin():
        log_id = await session.scalar(lookup)
    if log_id is None:
        raise AppError(ErrorCode.FEEDBACK_TRACE_MISMATCH)
    # Langfuse upserts by score ID. Scope a caller's retry token to the verified log.
    score_id = {"id": str(uuid5(log_id, submission_id.hex))} if submission_id is not None else {}
    try:
        record = await asyncio.to_thread(client.score.create, request=CreateScoreRequest(
            **score_id,
            trace_id=feedback.trace_id, name="user_feedback", value=feedback.score, comment=feedback.comment,
        ))
    except Exception as exc:
        logger.warning("Langfuse feedback delivery failed for log %s (%s)", log_id, type(exc).__name__)
        raise AppError(ErrorCode.FEEDBACK_UNAVAILABLE) from None
    return FeedbackData(feedback_id=record.id, trace_id=feedback.trace_id, log_id=log_id, score=feedback.score)
