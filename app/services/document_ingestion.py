import asyncio
import hashlib
import logging
import re
from pathlib import Path, PureWindowsPath
from uuid import UUID

from app.core.error_codes import ErrorCode
from app.core.exceptions import DocumentParseError, ServiceError
from app.providers.parsers.base import ParsedDocument
from app.providers.parsers.registry import get_document_parser


logger = logging.getLogger(__name__)


def validate_filename(name: str) -> None:
    if (not name or len(name.encode("utf-8")) > 255 or name.endswith((" ", "."))
            or PureWindowsPath(name).is_reserved() or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)):
        raise ServiceError(ErrorCode.PARAM_ERROR, detail="无效的上传文件名")
    if not get_document_parser(name).supports(name):
        raise ServiceError(ErrorCode.PARAM_ERROR, detail="仅支持 .pdf、.docx、.md 和 .txt，请将 .doc 另存为 docx")


def save_source(root: Path, tenant: str, identifier: UUID, filename: str, data: bytes) -> str:
    validate_filename(filename)
    component = tenant
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", tenant) or PureWindowsPath(tenant).is_reserved():
        component = "~" + hashlib.sha256(tenant.encode()).hexdigest()
    directory = root.resolve() / component / str(identifier)
    if not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("上传目录无效")
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / filename
    try:
        with path.open("xb") as stream:
            stream.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        directory.rmdir()
        raise
    return str(path)


def discard_source(path: str | None) -> None:
    if path:
        source = Path(path)
        try:
            source.unlink(missing_ok=True)
            if source.parent.is_dir():
                source.parent.rmdir()
        except OSError:
            logger.exception("DOCUMENT_FILE_CLEANUP_FAILED")


async def prepare_document_content(*, content: str | None, file_path: str | None, filename: str | None,
                                   source_type: str = "text", metadata: dict | None = None,
                                   reparse: bool = False) -> ParsedDocument:
    if content and content.strip() and not (reparse and file_path):
        return ParsedDocument(content, source_type, metadata or {})
    if not file_path or not filename:
        raise DocumentParseError("缺少文档原文或源文件")
    try:
        data = await asyncio.to_thread(Path(file_path).read_bytes)
    except OSError:
        raise DocumentParseError("无法读取源文件，请检查 Worker 的共享存储") from None
    try:
        parsed = await get_document_parser(filename).parse(data, filename=filename)
    except ServiceError:
        raise
    except ImportError:
        raise DocumentParseError('Worker 缺少解析依赖，请安装 .[parsers]') from None
    except Exception:
        hint = "，请另存为 docx 后重试" if filename.lower().endswith(".docx") else "，请检查文件格式"
        raise DocumentParseError("文档解析失败" + hint) from None
    if not parsed.content.strip() or "\x00" in parsed.content:
        raise DocumentParseError("文档正文为空或含无效字符")
    return parsed
