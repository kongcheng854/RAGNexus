from app.core.error_codes import ErrorCode
from app.core.exceptions import ServiceError
from app.providers.parsers.base import DocumentParser, ParsedDocument


class NoopParser(DocumentParser):
    async def parse(self, file_bytes: bytes, *, filename: str) -> ParsedDocument:
        raise ServiceError(ErrorCode.PARAM_ERROR, detail="仅支持 .docx、.pdf、.md 和 .txt 文件")
