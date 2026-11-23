"""Replace a knowledge base's entire settings object from one JSON file."""

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import standalone_session
from app.repositories.catalog import replace_knowledge_base_settings


from app.schemas.knowledge_base import KnowledgeSettings


async def replace_settings(kb_id: UUID, settings: KnowledgeSettings) -> None:
    async with standalone_session() as session, session.begin():
        if not await replace_knowledge_base_settings(session, kb_id, settings.model_dump(exclude_unset=True)):
            raise ValueError("知识库不存在")


def main() -> int:
    parser = argparse.ArgumentParser(description="用 JSON 文件完整覆盖指定知识库的 settings")
    parser.add_argument("--kb-id", type=UUID, required=True)
    parser.add_argument("--settings-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        settings = KnowledgeSettings.model_validate_json(args.settings_file.read_text(encoding="utf-8-sig"))
        factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=factory) as runner:
            runner.run(replace_settings(args.kb_id, settings))
    except ValidationError as exc:
        print(f"配置文件格式有误：{exc.errors(include_input=False)}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("写入失败，请检查数据库连接、迁移状态及 settings 内容", file=sys.stderr)
        return 1
    print(f"已更新 kb_id={args.kb_id}，synonyms 组数={len(settings.synonyms)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
