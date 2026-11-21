import logging
from time import perf_counter
from typing import Any

from app.core.error_codes import ErrorCode
from app.core.logging import log_event, payload_preview


logger = logging.getLogger(__name__)


def log_model_request(
    model: str, prompt: Any, *, operation: str, log_payloads: bool = False, limit: int = 2048
) -> float:
    log_event(
        logger, logging.INFO, "MODEL_REQUEST", model=model, operation=operation,
        prompt=payload_preview(prompt, limit) if log_payloads and logger.isEnabledFor(logging.INFO)
        else "[disabled]",
    )
    return perf_counter()


def log_model_response(
    model: str, result: Any, *, operation: str, started: float,
    log_payloads: bool = False, limit: int = 2048, **metrics: Any,
) -> None:
    log_event(
        logger, logging.INFO, "MODEL_RESPONSE", model=model, operation=operation,
        elapsed_ms=round((perf_counter() - started) * 1000, 2),
        result=payload_preview(result, limit) if log_payloads and logger.isEnabledFor(logging.INFO)
        else "[disabled]",
        **metrics,
    )


def log_model_failure(
    model: str, exc: Exception, *, operation: str, started: float, error: ErrorCode,
    upstream_status: int | None = None,
) -> None:
    log_event(
        logger, logging.ERROR, "MODEL_FAILURE", model=model, operation=operation,
        elapsed_ms=round((perf_counter() - started) * 1000, 2), code=int(error),
        upstream_status=upstream_status, exception_type=type(exc).__name__,
    )
