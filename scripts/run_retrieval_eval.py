"""Score fresh retrievals against human-reviewed references with RAGAS."""

import argparse
import asyncio
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import Settings
from app.core.error_codes import ErrorCode
from scripts.eval_dataset import PROFILES, ROOT, Case, Dataset, GoldenDataset, protect_golden_output, read_dataset, write_json

LOW_RECALL = 0.5


def make_judge(settings: Settings, *, max_workers=4, timeout=None):
    timeout = settings.llm_timeout_seconds if timeout is None else timeout
    model = settings.llm_model or "gpt-4o-mini"
    base, key = settings.model_base_url, settings.model_api_key.get_secret_value()
    if settings.llm_base_url and settings.llm_api_key.get_secret_value():
        base, key = settings.llm_base_url, settings.llm_api_key.get_secret_value()
    if not key:
        raise ValueError("请配置 MODEL_BASE_URL 和 MODEL_API_KEY，或完整的 LLM_BASE_URL 与 LLM_API_KEY")
    os.environ["RAGAS_DO_NOT_TRACK"] = "true"
    try:
        from langchain_openai import ChatOpenAI
        from ragas import SingleTurnSample
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import LLMContextPrecisionWithReference, LLMContextRecall
        from ragas.run_config import RunConfig
    except ImportError as exc:
        raise ValueError(f'无法加载评测依赖 {exc.name}，请执行 python -m pip install -e ".[eval]"') from None
    llm = LangchainLLMWrapper(
        ChatOpenAI(model=model, api_key=key, base_url=str(base), temperature=0,
                   timeout=timeout, max_retries=1),
        run_config=RunConfig(timeout=timeout, max_workers=max_workers, max_retries=2, seed=42),
    )
    # Preserve the configured ChatOpenAI temperature across concurrent reference scorers.
    if hasattr(llm, "bypass_temperature"):
        llm.bypass_temperature = True
    else:
        generate = llm.agenerate_text

        async def fixed_temperature(prompt, n=1, temperature=None, stop=None, callbacks=None):
            return await generate(prompt, n=n, temperature=0.0, stop=stop, callbacks=callbacks)

        llm.agenerate_text = fixed_temperature
    precision = LLMContextPrecisionWithReference(llm=llm)
    recall = LLMContextRecall(llm=llm)

    async def score(case: Case, contexts: list[str]) -> tuple[float, float]:
        sample = SingleTurnSample(user_input=case.question, reference=case.ground_truth, retrieved_contexts=contexts)
        try:
            values = (float(await precision.single_turn_ascore(sample, timeout=timeout)), float(await recall.single_turn_ascore(sample, timeout=timeout)))
        except Exception as exc:
            raise ValueError(f"RAGAS 裁判调用失败（{type(exc).__name__}），请检查模型名称、密钥、响应及端点") from None
        if not all(0 <= value <= 1 for value in values):
            raise ValueError("RAGAS 返回无效分数，请检查裁判模型输出")
        return values

    return model, score


async def retrieve(client: httpx.AsyncClient, kb_ids: tuple[UUID, ...], question: str, profile: str) -> list[str]:
    request = {"kb_ids": [str(kb) for kb in kb_ids], "user_id": "eval_runner", "query": question, "profile": profile}
    for attempt in range(3):
        try:
            response = await client.post("api/v1/rag/retrieve", json=request)
        except httpx.RequestError:
            raise ValueError("检索服务连接失败或超时，请检查 --base-url 与服务状态") from None
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        code = payload.get("code") if isinstance(payload, dict) else None
        if code == ErrorCode.API_RATE_LIMIT and response.status_code == 429 and attempt < 2:
            await asyncio.sleep(1)
            continue
        if not response.is_success or code != ErrorCode.SUCCESS:
            try:
                reason = ErrorCode(code).message
            except (TypeError, ValueError):
                reason = {401: "请检查租户 API Key", 403: "权限或套餐受限", 404: "请检查知识库和 API 地址"}.get(
                    response.status_code, "请检查服务地址与响应格式"
                )
            raise ValueError(f"检索失败（HTTP {response.status_code}，code={code}）：{reason}")
        try:
            chunks = payload["data"]["retrieved_chunks"]
            if not isinstance(chunks, list):
                raise TypeError
            contexts = [chunk["content"] for chunk in chunks]
            if not all(isinstance(content, str) and content.strip() for content in contexts):
                raise TypeError
        except (KeyError, TypeError):
            raise ValueError("检索响应缺少有效的 retrieved_chunks 或片段正文") from None
        return contexts


async def run(dataset: Dataset, args) -> dict:
    profile = args.profile or dataset.default_profile or "balanced"
    formal = isinstance(dataset, GoldenDataset)
    cases = list(enumerate(dataset.cases, 1))
    if not formal:
        cases = [(i, c) for i, c in cases if c.ground_truth.strip()]
    skipped = len(dataset.cases) - len(cases)
    print(f"{dataset.name} | profile={profile} | 待评 {len(cases)} 条，skipped {skipped} 条（ground_truth 为空）")
    rows = []
    model = None
    if cases:
        if not args.api_key:
            raise ValueError("请使用 --api-key 或设置 RAGNEXUS_API_KEY 提供租户密钥")
        scopes = {c.scope(dataset.kb_id) for _, c in cases}
        if args.kb_id and any(scope and args.kb_id not in scope for scope in scopes):
            raise ValueError("--kb-id 与数据集记录的知识库不一致，请检查数据集")
        if () in scopes and not args.kb_id:
            raise ValueError("请用 --kb-id 绑定知识库，或在测例填写 kb_ids 或 kb_id")
        base = httpx.URL(args.base_url)
        if base.scheme not in ("http", "https") or not base.host or base.query or base.fragment:
            raise ValueError("--base-url 需为检索服务的 HTTP(S) 基础地址")
        model, score = make_judge(Settings(_env_file=ROOT / ".env"))
        async with httpx.AsyncClient(base_url=str(base).rstrip("/") + "/", trust_env=False, timeout=120,
                                     headers={"Authorization": f"Bearer {args.api_key}"}) as client:
            for i, case in cases:
                kb_ids = case.scope(dataset.kb_id or args.kb_id)
                label = case.id or f"case_{i}"
                try:
                    contexts = await retrieve(client, kb_ids, case.question, profile)
                    precision, recall = await score(case, contexts) if contexts else (0.0, 0.0)
                except ValueError as exc:
                    raise ValueError(f"{label}：{exc}") from None
                row = {"id": label, "kb_id": str(kb_ids[0]), "kb_ids": [str(kb) for kb in kb_ids],
                       "multi_kb": len(kb_ids) > 1, "question": case.question, "chunk_count": len(contexts),
                       "context_precision": precision, "context_recall": recall}
                if formal:
                    row.update(kb_alias=case.kb_alias, suite=case.suite, case_type=case.case_type)
                rows.append(row)
                print(f"{label} | context_precision={precision:.4f} | context_recall={recall:.4f} | {case.question}")
    summary = {"total": len(dataset.cases), "evaluated": len(rows), "skipped": skipped}
    for metric in ("context_precision", "context_recall"):
        summary[metric] = math.fsum(row[metric] for row in rows) / len(rows) if rows else None
    low = [row for row in rows if row["context_recall"] < LOW_RECALL]
    if rows:
        print(f"平均 context_precision={summary['context_precision']:.4f} | context_recall={summary['context_recall']:.4f}")
    print(f"低召回（context_recall < {LOW_RECALL}）：{len(low)} 条")
    for row in low:
        print(f"  {row['id']} | {row['context_recall']:.4f} | {row['question']}")
    report = {"dataset": dataset.name, "profile": profile, "judge_model": model,
              "evaluated_at": datetime.now(timezone.utc).isoformat(), "summary": summary, "cases": rows,
              "low_recall_threshold": LOW_RECALL, "low_recall": [row["id"] for row in low]}
    if formal:
        report.update(dataset_version=dataset.version, schema_version=dataset.schema_version,
                      suite=dataset.selected_suite, knowledge_bases=dataset.knowledge_bases)
    for field in ("index_id", "settings_snapshot"):
        if value := getattr(args, field, None):
            report[field] = value
    return report


def main(argv=None, *, golden_only=False) -> int:
    parser = argparse.ArgumentParser(description="使用当前检索配置和人工标准答案评测检索精度与召回")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--api-key", default=os.getenv("RAGNEXUS_API_KEY"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--profile", choices=PROFILES)
    parser.add_argument("--kb-id", type=UUID, help="为尚未绑定的基线指定知识库")
    parser.add_argument("--suite", help="正式数据集的套件名，默认 all")
    parser.add_argument("--index-id", help="记录本轮实验的索引或环境标识")
    parser.add_argument("--settings-snapshot", help="记录冻结的知识库检索配置版本或快照引用")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output and args.dataset.resolve() == args.output.resolve():
            raise ValueError("报告路径与输入数据集相同，请指定其他 --output")
        dataset = read_dataset(args.dataset, args.suite, golden_only=golden_only)
        if args.output:
            protect_golden_output(args.output)
        report = asyncio.run(run(dataset, args))
        if args.output:
            write_json(args.output, report)
            print(f"报告已写入 {args.output}")
    except ValidationError:
        print("评测配置有误，请检查项目 .env 的字段格式。", file=sys.stderr)
        return 1
    except (ValueError, OSError, httpx.InvalidURL) as exc:
        print(f"评测失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
