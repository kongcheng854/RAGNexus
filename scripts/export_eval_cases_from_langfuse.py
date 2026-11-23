"""Export low-scoring Langfuse retrievals or merge locally reviewed cases."""

import argparse
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import httpx
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import Settings
from scripts.eval_dataset import DEFAULT_IMPORT, ROOT, Case, Dataset, merge_cases, protect_golden_output, read_dataset, write_json


def export_cases(client: httpx.Client, max_score: float, days: int | None, kb_id: UUID | None) -> Dataset:
    now = datetime.now(timezone.utc)
    params = {"name": "user_feedback", "operator": "<", "value": max_score, "limit": 100,
              "toTimestamp": now.isoformat().replace("+00:00", "Z")}
    if days is not None:
        params["fromTimestamp"] = (now - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    seen_traces = set()
    cases = {}
    skipped = 0
    page = 1
    while True:
        response = client.get("api/public/scores", params={**params, "page": page})
        payload = response.raise_for_status().json()
        scores = payload["data"]
        for score in scores:
            value, trace_id = score.get("value"), score.get("traceId")
            if score.get("name") != "user_feedback" or type(value) not in (float, int) or not value < max_score:
                continue
            if not trace_id or trace_id in seen_traces:
                continue
            seen_traces.add(trace_id)
            trace_response = client.get("api/public/traces/" + quote(trace_id, safe=""))
            if trace_response.status_code == 404:
                skipped += 1
                continue
            trace = trace_response.raise_for_status().json()
            meta = trace.get("metadata")
            if not isinstance(meta, dict):
                skipped += 1
                continue
            raw = trace.get("input")
            question = raw.get("query") if isinstance(raw, dict) else raw
            raw_ids = meta.get("kb_ids")
            if raw_ids is None:
                raw_ids = [meta.get("kb_id")]
            if not isinstance(raw_ids, list) or not raw_ids:
                skipped += 1
                continue
            try:
                scope = tuple(dict.fromkeys(UUID(str(item)) for item in raw_ids))
            except ValueError:
                skipped += 1
                continue
            if kb_id and kb_id not in scope:
                continue
            if trace.get("name") not in (None, "rag_retrieve") or not isinstance(question, str) or not question.strip():
                skipped += 1
                continue
            question = question.strip()
            key = (scope, question)
            if key not in cases:
                cases[key] = Case(id=f"lf_{trace_id}", kb_ids=list(scope), question=question, source={
                    "trace_id": trace_id, "log_id": meta.get("log_id"), "feedback_score": value,
                    "feedback_comment": score.get("comment"),
                })
        if page >= payload["meta"]["totalPages"]:
            break
        page += 1
    if skipped:
        print(f"跳过缺失或不适用的 trace：{skipped} 条", file=sys.stderr)
    scopes = {case.scope() for case in cases.values()}
    default_kb = kb_id if not scopes else None
    if len(scopes) == 1:
        ids = scopes.pop()
        if len(ids) == 1:
            default_kb = ids[0]
    return Dataset(name="imported_from_feedback", kb_id=default_kb,
                   default_profile="balanced", cases=list(cases.values()))


def apply_reviewed_references(imported: Dataset, reviewed: Dataset) -> None:
    references = {
        (case.scope(reviewed.kb_id), case.question): case.ground_truth
        for case in reviewed.cases if case.ground_truth.strip() and case.scope(reviewed.kb_id)
    }
    for case in imported.cases:
        reference = references.get((case.scope(imported.kb_id), case.question))
        if reference and not case.ground_truth.strip():
            case.ground_truth = reference


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="导出低分检索反馈，或合并人工审核的测例")
    parser.add_argument("--max-score", type=float, default=3, help="仅导出严格低于此分值的反馈（默认 3）")
    parser.add_argument("--days", type=int, help="只看最近 N 天，省略时不限起始时间")
    parser.add_argument("--kb-id", type=UUID)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--merge", type=Path, metavar="DATASET", help="保留目标数据集并按知识库和问题去重")
    parser.add_argument("--input", type=Path, help="使用已审核的本地文件作为合并来源，跳过网络导出")
    args = parser.parse_args(argv)
    if not math.isfinite(args.max_score) or args.days is not None and args.days <= 0:
        parser.error("--max-score 需为有限数值，--days 需为正整数")
    if args.input and not args.merge:
        parser.error("--input 需搭配 --merge 使用")
    output = args.output or args.merge or DEFAULT_IMPORT
    try:
        protect_golden_output(output)
        if args.merge and args.merge != output:
            protect_golden_output(args.merge)
        target = read_dataset(args.merge) if args.merge else None
        scope = args.kb_id or (target.kb_id if target else None)
        if args.input:
            imported = read_dataset(args.input)
        else:
            settings = Settings(_env_file=ROOT / ".env")
            secret = settings.langfuse_secret_key.get_secret_value()
            if not settings.langfuse_public_key or not secret:
                raise ValueError("请设置 LANGFUSE_PUBLIC_KEY 和 LANGFUSE_SECRET_KEY")
            with httpx.Client(base_url=str(settings.langfuse_host).rstrip("/") + "/",
                              auth=(settings.langfuse_public_key, secret), trust_env=False, timeout=30) as client:
                imported = export_cases(client, args.max_score, args.days, scope)
            if target and DEFAULT_IMPORT.is_file():
                apply_reviewed_references(imported, read_dataset(DEFAULT_IMPORT))
        result, added = merge_cases(target, imported, scope) if target else (imported, len(imported.cases))
        document = result.model_dump(mode="json", exclude_none=True)
        document.setdefault("kb_id", None)
        write_json(output, document)
        pending = sum(not c.ground_truth.strip() for c in result.cases)
        print(f"新增 {added} 条，共 {len(result.cases)} 条，待补 ground_truth {pending} 条。输出：{output}")
    except ValidationError:
        print("导出配置有误，请检查项目 .env 的字段格式。", file=sys.stderr)
        return 1
    except httpx.HTTPStatusError as exc:
        print(f"Langfuse 返回 HTTP {exc.response.status_code}，请检查项目密钥与服务地址。", file=sys.stderr)
        return 1
    except httpx.RequestError:
        print("无法连接 Langfuse，请检查 LANGFUSE_HOST 与服务状态。", file=sys.stderr)
        return 1
    except (KeyError, TypeError, AttributeError):
        print("Langfuse 返回的数据格式有误，请检查 Public API 地址。", file=sys.stderr)
        return 1
    except (ValueError, OSError, OverflowError) as exc:
        print(f"导出失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
