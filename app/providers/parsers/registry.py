from app.providers.parsers.base import DocumentParser
from app.providers.parsers.docx import DocxParser
from app.providers.parsers.markdown import MarkdownParser
from app.providers.parsers.noop import NoopParser
from app.providers.parsers.pdf import PdfParser


_PARSERS = (DocxParser(), PdfParser(), MarkdownParser())
_UNSUPPORTED = NoopParser()


def get_document_parser(filename: str) -> DocumentParser:
    return next((parser for parser in _PARSERS if parser.supports(filename)), _UNSUPPORTED)
