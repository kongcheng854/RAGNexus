import asyncio
from io import BytesIO
from zipfile import ZipFile

from app.core.exceptions import DocumentParseError
from app.providers.parsers.base import DocumentParser, ParsedDocument


class DocxParser(DocumentParser):
    extensions = (".docx",)

    async def parse(self, file_bytes: bytes, *, filename: str) -> ParsedDocument:
        return await asyncio.to_thread(self._convert, file_bytes)

    @staticmethod
    def _convert(data: bytes) -> ParsedDocument:
        import mammoth
        from defusedxml import ElementTree
        from markdownify import markdownify

        with ZipFile(BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > 128 * 1024 * 1024:
                raise DocumentParseError("Word 解压后过大，请拆分文档后上传")
            xml = ElementTree.fromstring(archive.read("word/document.xml"))
            if (any(name.startswith("word/embeddings/") for name in archive.namelist())
                    or any(node.tag.rsplit("}", 1)[-1] in {"txbxContent", "OLEObject"} for node in xml.iter())):
                raise DocumentParseError("Word 含嵌入对象或文本框，请转为普通段落后另存为 docx")
        result = mammoth.convert_to_html(BytesIO(data), convert_image=mammoth.images.img_element(lambda _: {}),
                                         external_file_access=False)
        content = markdownify(result.value, heading_style="ATX", table_infer_header=True, strip=["img"])
        return ParsedDocument(content.strip(), "docx", {"parser": "mammoth", "warnings": [w.message for w in result.messages]})
