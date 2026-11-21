import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import AwareDatetime, BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.auth import generate_api_key
from app.db.session import standalone_session
from app.models.api_key import ApiKey
from app.repositories.api_key_repository import ApiKeyRepository
from app.repositories.tenant_repository import TenantRepository
from app.schemas.common import Identifier, Title
from app.utils.id_generator import generate_id


class KeyArgs(BaseModel):
    tenant_id: Identifier
    name: Title
    expires_at: AwareDatetime | None = None


async def create_api_key(tenant_id: str, name: str, expires_at: datetime | None = None) -> str:
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise ValueError("过期时间必须晚于当前时间")
    async with standalone_session() as session:
        async with session.begin():
            tenant = await TenantRepository(session).get(tenant_id)
            if tenant is None or tenant.status != "active":
                raise ValueError("租户不存在或已禁用")
            plaintext, key_hash, prefix = generate_api_key()
            await ApiKeyRepository(session).create(ApiKey(
                id=generate_id(), tenant_id=tenant_id, key_hash=key_hash, key_prefix=prefix,
                name=name, status="active", expires_at=expires_at,
            ))
    return plaintext


def main() -> int:
    parser = argparse.ArgumentParser(description="为已有租户创建 API Key，仅显示一次明文")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--expires-at", help="可选的 ISO 8601 过期时间，必须包含时区")
    args = parser.parse_args()
    try:
        values = KeyArgs.model_validate(vars(args))
        factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=factory) as runner:
            plaintext = runner.run(create_api_key(values.tenant_id, values.name, values.expires_at))
    except ValidationError:
        print("参数错误，请检查租户 ID、名称和过期时间", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("数据库操作失败，请检查连接和迁移状态", file=sys.stderr)
        return 1
    print(plaintext)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
