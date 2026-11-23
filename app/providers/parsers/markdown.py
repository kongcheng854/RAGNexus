from pathlib import Path

from app.providers.parsers.base import DocumentParser, ParsedDocument
from app.core.exceptions import DocumentParseError


class MarkdownParser(DocumentParser):
    extensions = (".md", ".txt")

    async def parse(self, file_bytes: bytes, *, filename: str) -> ParsedDocument:
        try:
            text = file_bytes.decode("utf-8-sig").strip()
        except UnicodeDecodeError:
            raise DocumentParseError("文本编码无效，请保存为 UTF-8") from None
        kind = "markdown" if Path(filename).suffix.lower() == ".md" else "text"
        return ParsedDocument(text, kind, {"parser": "utf-8"})
