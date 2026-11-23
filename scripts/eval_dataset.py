"""Shared on-disk format for offline retrieval evaluation."""

import json
import os
import tempfile
from pathlib import Path
from typing import Annotated, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.tenant.plan_presets import Profile
from scripts.golden_dataset import is_golden, read_json, validate_golden

PROFILES = get_args(Profile)
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMPORT = ROOT / "eval/datasets/imported_from_feedback.json"


class Case(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str | None = None
    kb_id: UUID | None = None
    kb_ids: Annotated[list[UUID], Field(min_length=1)] | None = None
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    ground_truth: str = ""
    source: dict | None = None

    def scope(self, default: UUID | None = None) -> tuple[UUID, ...]:
        if self.kb_ids is not None:
            return tuple(dict.fromkeys(self.kb_ids))
        kb_id = self.kb_id or default
        return (kb_id,) if kb_id is not None else ()


class Dataset(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    kb_id: UUID | None = None
    default_profile: Profile | None = None
    cases: list[Case]


class GoldenDataset(Dataset):
    version: str
    schema_version: str
    knowledge_bases: dict[str, dict[str, str]]
    selected_suite: str


def read_dataset(path: Path, suite: str | None = None, *, golden_only: bool = False) -> Dataset:
    data = read_json(path)
    if golden_only or suite is not None or is_golden(data):
        return golden_suite(data, suite or "all")
    try:
        return Dataset.model_validate(data)
    except ValidationError as exc:
        issue = exc.errors(include_input=False, include_url=False)[0]
        field = ".".join(map(str, issue["loc"])) or "JSON"
        raise ValueError(f"测例文件 {path} 的 {field} 格式有误：{issue['msg']}") from None


def golden_suite(data: dict, selected: str) -> GoldenDataset:
    validate_golden(data)
    if selected not in data["suites"]:
        raise ValueError(f"未知套件 {selected}，可选套件：{', '.join(data['suites'])}")
    ids = data["suites"][selected]["case_ids"]
    cases = data["cases"]
    if ids != "all":
        by_id = {case["id"]: case for case in cases}
        cases = [by_id[case_id] for case_id in ids]
    kbs = data["knowledge_bases"]
    return GoldenDataset(name=data["name"], version=data["version"], schema_version=data["schema_version"],
                         selected_suite=selected, knowledge_bases=kbs,
                         cases=[Case(**case, kb_id=UUID(kbs[case["kb_alias"]]["kb_id"])) for case in cases])


def protect_golden_output(path: Path) -> None:
    if path.is_file():
        try:
            existing = read_json(path)
        except ValueError:
            return
        if is_golden(existing) and ("suites" in existing or "name" in existing or "version" in existing):
            raise ValueError(f"输出路径 {path} 已保存正式 Golden Set，请指定其他路径")


def write_json(path: Path, data: dict) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False)
    pending = Path(tmp.name)
    try:
        with tmp:
            tmp.write(content)
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def merge_cases(target: Dataset, incoming: Dataset, scope: UUID | None = None) -> tuple[Dataset, int]:
    """Preserve annotations for the same question and ordered retrieval scope."""
    if isinstance(target, GoldenDataset):
        raise ValueError("反馈候选不能合并到正式 Golden Set，请人工核对后创建新版本")
    if target.kb_id and scope and scope != target.kb_id:
        raise ValueError("--kb-id 与待合并数据集的知识库不一致")
    scope = scope or target.kb_id
    if not target.cases and not scope:
        scope = incoming.kb_id
    if not scope and any(not case.scope() for case in target.cases):
        scopes = {ids for case in incoming.cases if (ids := case.scope(incoming.kb_id))}
        if len(scopes) > 1 or any(len(ids) > 1 for ids in scopes):
            raise ValueError("主集尚未绑定知识库，请使用 --kb-id 指定要合并的知识库")
        scope = incoming.kb_id or next((ids[0] for ids in scopes), None)
    cases: dict[tuple[tuple[UUID, ...], str], Case] = {}
    for dataset in (target, incoming):
        for case in dataset.cases:
            ids = case.scope(dataset.kb_id or scope)
            if scope and scope not in ids:
                if dataset is target:
                    raise ValueError("待合并数据集含其他知识库，请检查 --kb-id 和主集范围")
                continue
            key = (ids, case.question)
            existing = cases.get(key)
            if existing is None:
                # A singleton inherited from the incoming dataset may need an explicit ID in the target.
                kb = ids[0] if ids and not case.kb_ids and ids != (scope,) else None
                cases[key] = case.model_copy(update={"kb_id": kb})
            elif not existing.ground_truth.strip() and case.ground_truth.strip():
                cases[key] = existing.model_copy(update={"ground_truth": case.ground_truth})
        if dataset is target:
            previous = len(cases)
    return target.model_copy(update={"kb_id": scope, "cases": list(cases.values())}), len(cases) - previous
