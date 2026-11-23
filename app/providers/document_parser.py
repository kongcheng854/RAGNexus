"""Compatibility imports for the former synchronous text-only parser."""

from app.providers.parsers.base import DocumentParser

__all__ = ["DocumentParser", "PlainTextParser"]


class PlainTextParser:
    def parse(self, content: str) -> str:
        return content.strip()
