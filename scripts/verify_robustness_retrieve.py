"""Verify retrieve boundaries and hybrid fallback against a running test API."""

import argparse
import os
import sys
from uuid import UUID

import httpx


def check(response: httpx.Response, status: int, code: int) -> dict:
    body = response.json()
    if not isinstance(body, dict) or (response.status_code, body.get("code")) != (status, code):
        actual = body.get("code") if isinstance(body, dict) else None
        raise ValueError(f"预期 HTTP {status} / code={code}，收到 HTTP {response.status_code} / code={actual}")
    return body


def verify(client: httpx.Client, args) -> None:
    body = {"kb_id": str(args.kb_id), "user_id": "robustness_check", "query": args.query, "profile": "speed"}
    path = "api/v1/rag/retrieve"
    for label, query in (("空白 query", " \t"), ("超长 query", "问" * (args.query_max_length + 1))):
        check(client.post(path, json={**body, "query": query}), 422, 20004)
        print(f"OK {label}返回 PARAM_ERROR")

    if not args.expect_es_down and not os.getenv("RAGNEXUS_TEST_ELASTICSEARCH_URL"):
        print("SKIP ES 故障验证：未指定测试 ES 环境。已停用测试 ES 时传 --expect-es-down。")
        return
    response = client.post(path, json={**body, "profile": "balanced"})
    data = check(response, 200, 0)["data"]
    details, chunks = data["metadata"]["retrieval"], data["retrieved_chunks"]
    if not details["degraded"] and not args.expect_es_down:
        print("OK hybrid 正常召回")
        print("SKIP ES 故障验证：关键词链路仍可用。")
        return
    if not details["degraded"] or details.get("degraded_reason") != "bm25 search failed":
        raise ValueError("hybrid 未按预期降级为向量检索，请核对测试 ES 状态")
    if not chunks or any(c.get("retrieval_source") != "vector" or c.get("vector_score") != c["score"] for c in chunks):
        raise ValueError("预期返回带原始向量分数的片段，请使用已有成功索引的单库")
    print(f"OK ES 不可用时 hybrid 返回 {len(chunks)} 个向量片段，code=0，degraded=true")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="检索 API 根地址")
    parser.add_argument("--kb-id", type=UUID, default=os.getenv("RAGNEXUS_TEST_KB_ID"))
    parser.add_argument("--api-key", default=os.getenv("RAGNEXUS_API_KEY"))
    parser.add_argument("--query", default="退款")
    parser.add_argument("--query-max-length", type=int, default=os.getenv("QUERY_MAX_LENGTH", "2000"),
                        help="与目标 API 的 QUERY_MAX_LENGTH 设置一致")
    parser.add_argument("--expect-es-down", action="store_true", help="测试 ES 已停用，严格验证回退")
    args = parser.parse_args(argv)
    if args.query_max_length < 1:
        parser.error("--query-max-length 必须为正整数")
    if not args.kb_id:
        if args.expect_es_down:
            parser.error("故障验证需要 --kb-id 或 RAGNEXUS_TEST_KB_ID 指定已索引知识库")
        print("SKIP query 边界：未提供测试知识库 --kb-id。")
        print("SKIP ES 故障验证：未提供已索引测试库和故障环境。")
        return 0
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    try:
        with httpx.Client(base_url=args.base_url.rstrip("/") + "/", headers=headers, timeout=60, trust_env=False) as client:
            verify(client, args)
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        print(f"FAIL 检索验证：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
