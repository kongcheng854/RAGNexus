import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import ErrorCode
from app.core.exceptions import AppError
from app.repositories.api_key_repository import ApiKeyRepository


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    tenant_name: str
    key_id: UUID | None = None
    key_prefix: str | None = None
    key_name: str | None = None
    plan: str = "free"


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    plaintext = "rk_live_" + secrets.token_hex(16)
    return plaintext, hash_api_key(plaintext), "rk_" + plaintext[8:16]


async def authenticate_api_key(session: AsyncSession, plaintext: str) -> TenantContext:
    if re.fullmatch(r"rk_live_[0-9a-f]{32}", plaintext) is None:
        raise AppError(ErrorCode.UNAUTHORIZED)
    async with session.begin():
        found = await ApiKeyRepository(session).find_by_hash(hash_api_key(plaintext))
        if found is None:
            raise AppError(ErrorCode.UNAUTHORIZED)
        key, tenant = found
        expired = key.expires_at is not None and key.expires_at <= datetime.now(timezone.utc)
        if key.status != "active" or tenant.status != "active" or expired:
            raise AppError(ErrorCode.UNAUTHORIZED)
        return TenantContext(
            tenant_id=tenant.id, tenant_name=tenant.name, key_id=key.id,
            key_prefix=key.key_prefix, key_name=key.name, plan=tenant.plan,
        )
