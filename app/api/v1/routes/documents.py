from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import SessionDependency, get_indexing_service
from app.api.v1.deps import TenantDependency
from app.schemas.common import ResponseEnvelope
from app.schemas.document import DocumentResult, DocumentUpload, DocumentUploadInput
from app.services.document_service import upload_document
from app.services.indexing_service import IndexingService


router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload")
async def upload(
    body: DocumentUpload,
    session: SessionDependency,
    tenant: TenantDependency,
    indexing: Annotated[IndexingService, Depends(get_indexing_service)],
) -> ResponseEnvelope[DocumentResult]:
    command = DocumentUploadInput(**body.model_dump(), tenant_id=tenant.tenant_id)
    return ResponseEnvelope[DocumentResult](data=await upload_document(session, command, indexing))
