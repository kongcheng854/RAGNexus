import asyncio
import json
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TypeVar

import httpx

from app.core.error_codes import ErrorCode
from app.core.model_logging import log_model_failure, log_model_request, log_model_response
from app.providers.llm.base import LLMProvider, LLMProviderError
from app.providers.openai_errors import upstream_error_code


Completion = TypeVar("Completion")
_attempts: ContextVar[Counter | None] = ContextVar("retrieval_completion_attempts", default=None)


@contextmanager
def count_completion_attempts():
    attempts = Counter()
    token = _attempts.set(attempts)
    try:
        yield attempts
    finally:
        _attempts.reset(token)


def reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


class OpenAICompatibleLLMProvider(LLMProvider):
    def __init__(
        self,
        client: httpx.AsyncClient,
        model: str,
        *,
        timeout_seconds: int = 60,
        log_payloads: bool = False,
        log_payload_limit: int = 2048,
    ) -> None:
        self.client = client
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.log_payloads = log_payloads
        self.log_payload_limit = log_payload_limit

    async def chat_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        temperature: float = 0.0,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        return await self._complete(
            system_prompt, user_payload, temperature, timeout_seconds, "chat_json",
            {"response_format": {"type": "json_object"}}, self._decode_json,
        )

    async def chat_text(
        self, *, system_prompt: str, user_payload: dict[str, Any], temperature: float,
        max_tokens: int, timeout_seconds: float,
    ) -> str:
        return await self._complete(
            system_prompt, user_payload, temperature, timeout_seconds, "chat_text",
            {"max_tokens": max_tokens}, self._decode_text,
        )

    async def _complete(
        self, system_prompt: str, user_payload: dict[str, Any], temperature: float,
        timeout_seconds: float | None, operation: str, options: dict[str, Any],
        decode: Callable[[httpx.Response], Completion],
    ) -> Completion:
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        started = log_model_request(
            self.model, {"system": system_prompt, "user": user_payload},
            operation=operation, log_payloads=self.log_payloads, limit=self.log_payload_limit,
        )
        upstream_status = None
        try:
            async with asyncio.timeout(timeout):
                if (attempts := _attempts.get()) is not None:
                    attempts[operation] += 1
                response = await self.client.post(
                    "chat/completions",
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {
                                "role": "user",
                                "content": json.dumps(user_payload, ensure_ascii=False, allow_nan=False),
                            },
                        ],
                        "temperature": temperature,
                        **options,
                    },
                    timeout=timeout,
                )
                upstream_status = response.status_code
                response.raise_for_status()
                result = decode(response)
        except (httpx.HTTPError, TimeoutError, LLMProviderError) as exc:
            if isinstance(exc, LLMProviderError):
                error = exc.error
            elif isinstance(exc, (httpx.TimeoutException, TimeoutError)):
                error = ErrorCode.LLM_TIMEOUT
            elif isinstance(exc, httpx.HTTPStatusError):
                error = upstream_error_code(exc.response)
            else:
                error = ErrorCode.LLM_ERROR

            log_model_failure(
                self.model, exc, operation=operation, started=started,
                error=error, upstream_status=upstream_status,
            )
            if isinstance(exc, LLMProviderError):
                raise
            raise LLMProviderError(error=error) from exc

        log_model_response(
            self.model, result, operation=operation, started=started, upstream_status=upstream_status,
            log_payloads=self.log_payloads, limit=self.log_payload_limit,
        )
        return result

    @staticmethod
    def _decode_text(response: httpx.Response) -> str:
        try:
            choice = response.json()["choices"][0]
            if choice.get("finish_reason") == "content_filter":
                raise LLMProviderError(error=ErrorCode.LLM_CONTENT_VIOLATION)
            if choice.get("finish_reason") == "length":
                raise LLMProviderError("chat response was truncated", error=ErrorCode.LLM_NO_RESPONSE)
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("chat message content must be text")
            return content
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            raise LLMProviderError("invalid chat response", error=ErrorCode.LLM_NO_RESPONSE) from exc

    @classmethod
    def _decode_json(cls, response: httpx.Response) -> dict[str, Any]:
        try:
            result = json.loads(cls._decode_text(response), parse_constant=reject_json_constant)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, RecursionError) as exc:
            raise LLMProviderError(
                "invalid chat JSON object", error=ErrorCode.LLM_NO_RESPONSE
            ) from exc

        if not isinstance(result, dict):
            raise LLMProviderError("chat result must be an object", error=ErrorCode.LLM_NO_RESPONSE)
        return result
