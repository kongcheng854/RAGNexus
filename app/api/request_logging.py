import json
import logging
from collections.abc import Callable
from time import perf_counter

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import error_response, log_exception
from app.core.error_codes import ErrorCode
from app.core.logging import log_event, payload_preview, request_id
from app.utils.id_generator import generate_id


logger = logging.getLogger(__name__)


class BodyCapture:
    def __init__(self, limit: int, enabled: bool) -> None:
        self.limit = limit
        self.enabled = enabled
        self.size = 0
        self.buffer = bytearray()
        self.complete = False

    def append(self, body: bytes, more: bool) -> None:
        self.size += len(body)
        self.complete = not more
        if self.enabled and self.size <= self.limit:
            self.buffer.extend(body)

    def preview(self, content_type: str) -> str | None:
        if not self.enabled:
            return "[disabled]"
        if not self.complete:
            return "[not captured completely]"
        if not self.size:
            return None
        if self.size > self.limit:
            return "[body exceeds capture limit]"
        media_type = content_type.partition(";")[0].strip().lower()
        if media_type != "application/json" and not media_type.endswith("+json"):
            return "[non-JSON body omitted]"
        try:
            return payload_preview(json.loads(self.buffer), self.limit)
        except (ValueError, RecursionError):
            return "[invalid JSON omitted]"


def log_api_request(
    *, log_payloads: bool = False, limit: int = 2048
) -> Callable[[ASGIApp], ASGIApp]:
    """Decorate an ASGI callable to log HTTP traffic and catch unexpected exceptions."""

    def decorate(app: ASGIApp) -> ASGIApp:
        async def logged(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                await app(scope, receive, send)
                return

            incoming = Request(scope)
            identifier = str(generate_id())
            token = request_id.set(identifier)
            started = perf_counter()
            capture = log_payloads and logger.isEnabledFor(logging.INFO)
            request_body = BodyCapture(limit, capture)
            response_body = BodyCapture(limit, capture)
            response_content_type = ""
            status = None

            async def logged_receive() -> Message:
                message = await receive()
                if message["type"] == "http.request":
                    request_body.append(message.get("body", b""), message.get("more_body", False))
                return message

            async def logged_send(message: Message) -> None:
                nonlocal status, response_content_type
                if message["type"] == "http.response.start":
                    status = message["status"]
                    headers = MutableHeaders(scope=message)
                    headers["X-Request-ID"] = identifier
                    response_content_type = headers.get("content-type", "")
                elif message["type"] == "http.response.body":
                    response_body.append(message.get("body", b""), message.get("more_body", False))
                await send(message)

            try:
                await app(scope, logged_receive, logged_send)
            except Exception as exc:
                log_exception(incoming, exc, ErrorCode.SERVER_ERROR)
                if status is not None:
                    raise
                response = error_response(incoming, ErrorCode.SERVER_ERROR)
                await response(scope, logged_receive, logged_send)
            finally:
                try:
                    method = scope["method"]
                    path = scope["path"]
                    log_event(
                        logger, logging.INFO, "API_REQUEST", method=method, path=path,
                        body_bytes_read=request_body.size,
                        body=request_body.preview(Headers(scope=scope).get("content-type", "")),
                    )
                    log_event(
                        logger, logging.INFO, "API_RESPONSE", method=method, path=path, status=status,
                        error_code=getattr(incoming.state, "error_code", None),
                        elapsed_ms=round((perf_counter() - started) * 1000, 2),
                        body_bytes=response_body.size, body=response_body.preview(response_content_type),
                    )
                finally:
                    request_id.reset(token)

        return logged

    return decorate
