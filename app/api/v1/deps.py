import logging
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies import SessionDependency
from app.core.auth import TenantContext, authenticate_api_key
from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.core.logging import log_event


logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


async def get_current_tenant(
    request: Request,
    session: SessionDependency,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> TenantContext:
    if not request.app.state.settings.auth_enabled:
        return TenantContext(tenant_id="tenant_demo", tenant_name="演示租户")
    if credentials is None:
        raise AppError(ErrorCode.UNAUTHORIZED)
    tenant = await authenticate_api_key(session, credentials.credentials)
    log_event(
        logger, logging.INFO, "AUTHENTICATED",
        tenant_id=tenant.tenant_id, key_id=tenant.key_id, key_prefix=tenant.key_prefix,
    )
    return tenant


TenantDependency = Annotated[TenantContext, Depends(get_current_tenant)]
