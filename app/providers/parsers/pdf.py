import asyncio
from threading import Lock

from app.core.exceptions import DocumentParseError
from app.providers.parsers.base import DocumentParser, ParsedDocument


# MuPDF documents must not be processed concurrently in threads of the same worker process.
_pdf_lock = Lock()


class PdfParser(DocumentParser):
    extensions = (".pdf",)

    async def parse(self, file_bytes: bytes, *, filename: str) -> ParsedDocument:
        return await asyncio.to_thread(self._extract, file_bytes)

    @staticmethod
    def _extract(data: bytes) -> ParsedDocument:
        import pymupdf

        with _pdf_lock, pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass:
                raise DocumentParseError("PDF 已加密，请解除密码后重新上传")
            parts = []
            for index, page in enumerate(doc, 1):
                text = page.get_text("text").strip()
                if text:
                    parts.append(f"## 第 {index} 页\n\n{text}")
            if not parts:
                raise DocumentParseError("疑似扫描件，本期不支持 OCR")
            return ParsedDocument("\n\n".join(parts), "pdf", {"parser": "pymupdf", "page_count": len(doc)})
