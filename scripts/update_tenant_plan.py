import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import standalone_session
from app.models.tenant import Tenant
from app.tenant.plan_presets import PLAN_PRESETS


async def update_plan(tenant_id: str, plan: str) -> None:
    async with standalone_session() as session, session.begin():
        updated = await session.scalar(update(Tenant).where(Tenant.id == tenant_id).values(plan=plan).returning(Tenant.id))
        if updated is None:
            raise ValueError("租户不存在，请先运行 scripts/create_tenant.py")


def main() -> int:
    parser = argparse.ArgumentParser(description="更新租户套餐")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--plan", required=True, choices=PLAN_PRESETS)
    args = parser.parse_args()
    try:
        factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=factory) as runner:
            runner.run(update_plan(args.tenant_id, args.plan))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("数据库操作失败，请检查连接和迁移状态", file=sys.stderr)
        return 1
    print(f"已更新租户 {args.tenant_id} 的套餐为 {args.plan}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
