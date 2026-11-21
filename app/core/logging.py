import asyncio
import json
import logging
import re
import sys
from collections.abc import Iterator
from contextvars import ContextVar
from copy import copy
from itertools import islice
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from queue import Full, Queue
from typing import Any


request_id: ContextVar[str] = ContextVar("request_id", default="-")
_API_KEY_PATTERN = re.compile(r"rk_live_[0-9a-f]{32}")

_COLORS = {
    logging.DEBUG: "\x1b[36m",
    logging.INFO: "\x1b[32m",
    logging.WARNING: "\x1b[33m",
    logging.ERROR: "\x1b[31m",
    logging.CRITICAL: "\x1b[35m",
}
_SECRET_PARTS = (
    "password", "passwd", "secret", "token", "apikey", "authorization", "cookie",
    "credential", "privatekey", "databaseurl",
)


def log_event(
    logger: logging.Logger, level: int, event: str, *, exc_info: Any = None, **fields: Any
) -> None:
    logger.log(level, event, extra={"fields": fields}, exc_info=exc_info, stacklevel=2)


class EventFormatter(logging.Formatter):
    def __init__(self, color: bool = False) -> None:
        super().__init__(
            "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d"
            " | request_id=%(request_id)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
            defaults={"request_id": "-"},
        )
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        fields = getattr(record, "fields", None)
        if fields:
            first, sep, rest = line.partition("\n")
            line = first + " | " + json.dumps(fields, ensure_ascii=False, default=str) + sep + rest
        line = _API_KEY_PATTERN.sub("rk_live_[redacted]", line)
        if self.color and record.levelno in _COLORS:
            return f"{_COLORS[record.levelno]}{line}\x1b[0m"
        return line


class AsyncLogHandler(QueueHandler):
    def __init__(self, queue: Queue) -> None:
        super().__init__(queue)
        self.dropped = 0

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        # Preserve exceptions for formatting on the listener thread.
        snapshot = copy(record)
        snapshot.request_id = request_id.get()
        return snapshot

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            super().enqueue(record)
        except Full:
            self.dropped += 1


class DrainingListener(QueueListener):
    def enqueue_sentinel(self) -> None:
        # Shutdown runs on a worker thread. Wait for room to flush accepted records.
        self.queue.put(self._sentinel)


class AppLogging:
    def __init__(
        self, *, directory: Path, level: str, max_bytes: int, backups: int,
        queue_size: int, color: bool,
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        console = logging.StreamHandler()
        console.setFormatter(EventFormatter(color and sys.stderr.isatty()))
        handlers: list[logging.Handler] = [console]
        try:
            for filename, minimum in (("app.log", logging.NOTSET), ("error.log", logging.ERROR)):
                handler = RotatingFileHandler(
                    directory / filename, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
                )
                handlers.append(handler)
                handler.setLevel(minimum)
                handler.setFormatter(EventFormatter())
        except Exception:
            for handler in handlers:
                handler.close()
            raise

        self.logger = logging.getLogger("app")
        self.previous = self.logger.level, self.logger.propagate
        self.level = level
        self.queue_handler = AsyncLogHandler(Queue(maxsize=queue_size))
        self.handlers = handlers
        self.listener = DrainingListener(
            self.queue_handler.queue, *handlers, respect_handler_level=True
        )

    def start(self) -> None:
        self.listener.start()
        self.logger.setLevel(self.level)
        self.logger.propagate = False
        self.logger.addHandler(self.queue_handler)

    async def stop(self) -> None:
        self.logger.removeHandler(self.queue_handler)
        level, propagate = self.previous
        self.logger.setLevel(level)
        self.logger.propagate = propagate
        await asyncio.to_thread(self._close)

    def _close(self) -> None:
        self.listener.stop()
        if self.queue_handler.dropped:
            record = self.logger.makeRecord(
                self.logger.name, logging.WARNING, __file__, 0,
                "LOG_QUEUE_DROPPED | records=%d", (self.queue_handler.dropped,), None,
                "_close",
            )
            self.listener.handle(record)
        self.queue_handler.close()
        for handler in self.handlers:
            handler.close()


def _limit_payload(value: Any, limit: int, budget: Iterator[int], depth: int = 0) -> Any:
    if depth >= 5 or next(budget, None) is None:
        return "[preview limit]"
    if isinstance(value, dict):
        result = {}
        for key, item in islice(value.items(), 16):
            key = str(key)
            normalized = "".join(ch for ch in key.lower() if ch.isalnum())
            sensitive = any(part in normalized for part in _SECRET_PARTS)
            result[key[:128]] = (
                "[redacted]" if sensitive else _limit_payload(item, limit, budget, depth + 1)
            )
        if len(value) > 16:
            result["[omitted keys]"] = len(value) - 16
        return result
    if isinstance(value, (list, tuple)):
        result = [_limit_payload(item, limit, budget, depth + 1) for item in value[:16]]
        return result + [f"[{len(value) - 16} items omitted]"] if len(value) > 16 else result
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    return value


def payload_preview(value: Any, limit: int) -> str:
    bounded = _limit_payload(value, min(limit, 512), iter(range(64)))
    encoded = json.dumps(bounded, ensure_ascii=False, default=str)
    if len(encoded) > limit:
        return encoded[:limit] + "…"
    return encoded
