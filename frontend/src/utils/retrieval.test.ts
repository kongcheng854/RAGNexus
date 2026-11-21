import { describe, expect, it } from "vitest";
import { buildRetrievalRequest, formatScore, retrievalSchema, type RetrievalValues } from "./retrieval";
import { readScope, scopeUrl } from "./navigation";

const values: RetrievalValues = {
  kb_id: "939a5baf-650a-44a7-8620-73f8ea878139",
  query: "退款规则", top_k: 5, mode: "hybrid", vector_top_k: 20,
  bm25_top_k: 20, rrf_k: 60, rerank_enabled: false, rerank_top_n: 5,
};

describe("retrieval request contract", () => {
  it("omits rerank when unchecked and includes hybrid parameters", () => {
    const payload = buildRetrievalRequest(values);
    expect(payload).not.toHaveProperty("rerank_options");
    expect(payload).not.toHaveProperty("tenant_id");
    expect(payload.user_id).toBe("debug_user");
    expect(payload.retrieval_options).toEqual({
      mode: "hybrid", vector_top_k: 20, bm25_top_k: 20, rrf_k: 60,
    });
  });
  it("only sends hybrid parameters in hybrid mode", () => {
    const payload = buildRetrievalRequest({ ...values, mode: "bm25", rerank_enabled: true });
    expect(payload.retrieval_options).toEqual({ mode: "bm25" });
    expect(payload.rerank_options).toEqual({ enabled: true, top_n: 5 });
  });
  it("validates active parameters without blocking a hidden parameter", () => {
    expect(retrievalSchema.safeParse({ ...values, vector_top_k: NaN }).success).toBe(false);
    expect(retrievalSchema.safeParse({
      ...values, mode: "vector", vector_top_k: NaN, rerank_top_n: NaN,
    }).success).toBe(true);
    expect(retrievalSchema.safeParse({ ...values, top_k: 0 }).success).toBe(false);
    expect(retrievalSchema.safeParse({ ...values, kb_id: "invalid" }).success).toBe(false);
  });
  it("renders zero as a score and roundtrips scope parameters", () => {
    expect(formatScore(0)).toBe("0.000000");
    expect(formatScore(null)).toBe("—");
    const url = scopeUrl("/retrieve", values.kb_id);
    expect(url).not.toContain("tenant_id");
    expect(readScope(new URL(url, "http://test").searchParams)).toEqual({
      kbId: values.kb_id, valid: true,
    });
  });
});
