"""Upload generated samples to a test KB, wait for indexing and check the retrieved text."""

import argparse
import asyncio
from io import BytesIO
import os
import sys
from time import monotonic
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import httpx


def sample_docx(token: str) -> bytes:
    from xml.sax.saxutils import escape

    token = escape(token)
    word = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    types = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    doc = f'<w:document xmlns:w="{word}"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Refund policy</w:t></w:r></w:p>'
    doc += '<w:tbl>'
    for row in [("Policy", "Approval"), ("Refund voucher", token)]:
        doc += "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>" for cell in row) + "</w:tr>"
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types +
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
            ''.join(f'<Override PartName="/word/{name}.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml"/>'
                    for name, kind in [("document", "document.main"), ("styles", "styles")]) + '</Types>')
        archive.writestr("_rels/.rels", f'<Relationships xmlns="{pkg}"><Relationship Id="r1" Type="{rel}/officeDocument" Target="word/document.xml"/></Relationships>')
        archive.writestr("word/_rels/document.xml.rels", f'<Relationships xmlns="{pkg}"><Relationship Id="r1" Type="{rel}/styles" Target="styles.xml"/></Relationships>')
        archive.writestr("word/styles.xml", f'<w:styles xmlns:w="{word}"><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style></w:styles>')
        archive.writestr("word/document.xml", doc + "</w:tbl></w:body></w:document>")
    return buffer.getvalue()


def sample_pdf(text: str) -> bytes:
    import pymupdf

    with pymupdf.open() as document:
        page = document.new_page()
        if text:
            page.insert_text((54, 72), text)
        return document.tobytes()


async def verify(client: httpx.AsyncClient, kb: str, timeout: int) -> None:
    suffix = uuid4().hex[:8]
    samples = [("docx", "Refund approval " + suffix), ("pdf", "Parcel tracking " + suffix),
               ("txt", "Reward points " + suffix), ("md", "Shipping deadline " + suffix)]
    for kind, marker in samples:
        filename = f"parser-{suffix}.{kind}"
        if kind == "md":
            response = await client.post("documents/upload", json={"kb_id": kb, "title": filename,
                                                                    "content": "# Shipping\n" + marker})
        else:
            data = sample_docx(marker) if kind == "docx" else sample_pdf(marker) if kind == "pdf" else marker.encode()
            response = await client.post("documents/upload", data={"kb_id": kb}, files={"file": (filename, data)})
        identifier = response.raise_for_status().json()["data"]["document_id"]
        deadline = monotonic() + timeout
        while True:
            status = (await client.get(f"documents/{identifier}")).raise_for_status().json()["data"]
            if status["status"] == 1:
                break
            if status["status"] == 2 or monotonic() >= deadline:
                raise ValueError(f"{kind} {identifier}: {status['error_message'] or '等待索引超时'}")
            await asyncio.sleep(0.5)
        response = await client.post("rag/retrieve", json={"kb_id": kb, "user_id": "parser_verify",
                                                          "query": marker, "profile": "speed"})
        hits = response.raise_for_status().json()["data"]["retrieved_chunks"]
        match = next((c for c in hits if c["document_id"] == identifier and marker in c["content"]), None)
        if match is None:
            raise ValueError(f"{kind} {identifier}: 未召回预期正文，请使用专用测试知识库")
        metadata = match.get("metadata") or {}
        if kind == "docx" and (metadata.get("heading_path"), metadata.get("chunk_type")) != ("Refund policy", "table"):
            raise ValueError("Word 标题或表格结构未保留")
        if kind == "pdf" and metadata.get("heading_path") != "第 1 页":
            raise ValueError("PDF 页码未保留")
        print(f"{kind} 通过，document_id={identifier}", flush=True)


async def run(args) -> None:
    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/") + "/api/v1/",
                                 headers={"Authorization": f"Bearer {args.api_key}"}, timeout=60) as client:
        await verify(client, str(args.kb_id), args.timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description="向测试知识库上传四份生成的样本并验证检索")
    parser.add_argument("--kb-id", type=UUID, required=True)
    parser.add_argument("--api-key", default=os.getenv("RAGNEXUS_API_KEY"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (ValueError, ImportError, httpx.HTTPError) as exc:
        print(f"验收失败：{exc}", file=sys.stderr)
        return 1
    print("四种格式全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
