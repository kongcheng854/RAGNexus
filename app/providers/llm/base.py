from abc import ABC, abstractmethod
from typing import Any

from app.core.error_codes import ErrorCode
from app.core.exceptions import ServiceError


class LLMProviderError(ServiceError):
    def __init__(
        self, detail: str | None = None, *, error: ErrorCode = ErrorCode.LLM_ERROR
    ) -> None:
        super().__init__(error, detail=detail)


class LLMProvider(ABC):
    @abstractmethod
    async def chat_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        temperature: float = 0.0,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Return a JSON object or raise LLMProviderError."""
        raise NotImplementedError
