import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db.session import standalone_session
from app.models.tenant import Tenant
from app.repositories.tenant_repository import TenantRepository
from app.schemas.common import Identifier, Title


class TenantArgs(BaseModel):
    id: Identifier
    name: Title


async def create_tenant(tenant_id: str, name: str) -> None:
    async with standalone_session() as session:
        async with session.begin():
            repository = TenantRepository(session)
            if await repository.get(tenant_id) is not None:
                raise ValueError("租户已存在")
            await repository.create(Tenant(id=tenant_id, name=name, status="active"))


def main() -> int:
    parser = argparse.ArgumentParser(description="注册租户")
    parser.add_argument("--id", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    try:
        values = TenantArgs.model_validate(vars(args))
        factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=factory) as runner:
            runner.run(create_tenant(values.id, values.name))
    except IntegrityError:
        print("租户已存在或数据冲突", file=sys.stderr)
        return 1
    except ValidationError:
        print("参数错误，请检查 ID 和名称", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("数据库操作失败，请检查连接和迁移状态", file=sys.stderr)
        return 1
    print("租户创建成功")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
