from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.api.dependencies import KeywordDependency, SessionDependency, SettingsDependency
from app.api.v1.deps import TenantDependency
from app.schemas.common import ResponseEnvelope
from app.core.error_codes import ErrorCode
from app.core.exceptions import ServiceError
from app.services.document_ingestion import validate_filename
from app.schemas.document import DocumentFileFields, DocumentDetail, DocumentResult, DocumentUpload, DocumentUploadInput
from app.tenant.plan_resolver import resolve_plan
from app.services import document_service


router = APIRouter(prefix="/documents", tags=["documents"])


class UploadTooLarge(MultiPartException):
    pass


async def read_upload(request: Request, limit: int) -> tuple[DocumentFileFields, str, bytes]:
    async def limited_stream():
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit + 64 * 1024:  # Allow space for multipart framing and text fields.
                raise UploadTooLarge("upload too large")
            yield chunk

    try:
        form = await MultiPartParser(request.headers, limited_stream(), max_files=1, max_fields=2).parse()
    except UploadTooLarge:
        raise ServiceError(ErrorCode.PAYLOAD_TOO_LARGE) from None
    except (MultiPartException, ValueError):
        raise ServiceError(ErrorCode.PARAM_ERROR, detail="无效的 multipart 表单") from None
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile) or not upload.filename:
            raise ServiceError(ErrorCode.PARAM_ERROR, detail="缺少 file 文件")
        validate_filename(upload.filename)
        if upload.size is not None and upload.size > limit:
            raise ServiceError(ErrorCode.PAYLOAD_TOO_LARGE)
        fields = DocumentFileFields.model_validate({
            "kb_id": form.get("kb_id"), "title": form.get("title", Path(upload.filename).stem),
        })
        data = await upload.read(limit + 1)
        if len(data) > limit:
            raise ServiceError(ErrorCode.PAYLOAD_TOO_LARGE)
        return fields, upload.filename, data
    finally:
        await form.close()


@router.post("/upload", openapi_extra={"requestBody": {"required": True, "content": {
    "application/json": {"schema": {"$ref": "#/components/schemas/DocumentUpload"}},
    "multipart/form-data": {"schema": {"type": "object", "required": ["file", "kb_id"], "properties": {
        "file": {"type": "string", "format": "binary"}, "kb_id": {"type": "string", "format": "uuid"},
        "title": {"type": "string", "maxLength": 255},
    }}},
}}})
async def upload(request: Request, session: SessionDependency, tenant: TenantDependency,
                 settings: SettingsDependency) -> ResponseEnvelope[DocumentResult]:
    media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    limits = resolve_plan(tenant).limits
    try:
        if media_type == "multipart/form-data":
            fields, name, data = await read_upload(request, settings.document_max_size_mb * 1024 * 1024)
            document = await document_service.create_file_record(session, fields, tenant.tenant_id, limits,
                                                                 name, data, settings.document_storage_path)
        elif media_type in ("", "application/json") or media_type.endswith("+json"):
            body = DocumentUpload.model_validate_json(await request.body())
            command = DocumentUploadInput(**body.model_dump(), tenant_id=tenant.tenant_id)
            document = await document_service.create_document_record(session, command, limits)
        else:
            raise ServiceError(ErrorCode.UNSUPPORTED_MEDIA_TYPE)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_input=False)) from None
    return ResponseEnvelope(data=await document_service.enqueue_document(session, document))


@router.get("/{document_id}")
async def detail(document_id: UUID, session: SessionDependency, tenant: TenantDependency) -> ResponseEnvelope[DocumentDetail]:
    return ResponseEnvelope(data=await document_service.get_document(session, tenant.tenant_id, document_id))


@router.delete("/{document_id}")
async def remove(
    document_id: UUID, session: SessionDependency, tenant: TenantDependency, keyword: KeywordDependency,
) -> ResponseEnvelope[None]:
    await document_service.delete_document(session, keyword, tenant.tenant_id, document_id)
    return ResponseEnvelope(data=None)


@router.post("/{document_id}/reindex")
async def reindex(
    document_id: UUID, session: SessionDependency, tenant: TenantDependency, keyword: KeywordDependency, reparse: bool = False,
) -> ResponseEnvelope[DocumentResult]:
    return ResponseEnvelope(data=await document_service.reindex_document(session, keyword, tenant.tenant_id, document_id, resolve_plan(tenant).limits, reparse=reparse))
