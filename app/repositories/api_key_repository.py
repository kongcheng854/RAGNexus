from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey
from app.models.tenant import Tenant


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_by_hash(self, key_hash: str) -> tuple[ApiKey, Tenant] | None:
        result = await self.session.execute(
            select(ApiKey, Tenant).join(Tenant, ApiKey.tenant_id == Tenant.id)
            .where(ApiKey.key_hash == key_hash)
        )
        row = result.one_or_none()
        return (row[0], row[1]) if row else None

    async def create(self, key: ApiKey) -> None:
        self.session.add(key)
        await self.session.flush()
