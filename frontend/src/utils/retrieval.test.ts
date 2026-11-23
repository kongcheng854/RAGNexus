import { describe, expect, it } from "vitest";
import { buildRetrievalRequest, formatScore, retrievalSchema, selectProfile, type RetrievalValues } from "./retrieval";
import { readScope, scopeUrl } from "./navigation";

import type { AuthMeData } from "../types/api";

const pro: AuthMeData["features"] = {
  allowed_profiles: ["speed", "balanced", "quality", "custom"],
  hybrid_allowed: true, rerank_allowed: true, query_rewrite_allowed: true,
};
const standard: AuthMeData["features"] = {
  allowed_profiles: ["speed", "balanced", "custom"],
  hybrid_allowed: true, rerank_allowed: false, query_rewrite_allowed: false,
};

const values: RetrievalValues = {
  kb_ids: ["939a5baf-650a-44a7-8620-73f8ea878139"],
  query: "退款规则", top_k: 5, mode: "hybrid", vector_top_k: 20,
  bm25_top_k: 20, rrf_k: 60, rewrite_enabled: false, rerank_enabled: false, rerank_top_n: 5,
};

describe("retrieval request contract", () => {
  it("omits rerank when unchecked and includes hybrid parameters", () => {
    const payload = buildRetrievalRequest(values, "custom", pro);
    expect(payload).not.toHaveProperty("rerank_options");
    expect(payload).not.toHaveProperty("query_options");
    expect(buildRetrievalRequest({ ...values, rewrite_enabled: true }, "custom", pro).query_options)
      .toEqual({ enabled: true, strategy: "rewrite" });
    expect(payload).not.toHaveProperty("tenant_id");
    expect(payload.profile).toBe("custom");
    expect(buildRetrievalRequest(values, "speed", pro)).toEqual({ profile: "speed", kb_id: values.kb_ids[0], query: values.query, user_id: "debug_user" });
    expect(payload.user_id).toBe("debug_user");
    expect(payload.retrieval_options).toEqual({
      mode: "hybrid", vector_top_k: 20, bm25_top_k: 20, rrf_k: 60,
    });
  });
  it("requires a selection and preserves the chosen order for joint retrieval", () => {
    expect(retrievalSchema("speed", pro).safeParse({ ...values, kb_ids: [] }).success).toBe(false);
    const kb_ids = ["33eff430-dafd-4814-983a-60064e43404a", ...values.kb_ids];
    for (const profile of ["balanced", "custom"] as const) {
      const payload = buildRetrievalRequest({ ...values, kb_ids }, profile, pro);
      expect(payload.kb_ids).toEqual(kb_ids);
      expect(payload).not.toHaveProperty("kb_id");
    }
  });
  it("only sends hybrid parameters in hybrid mode", () => {
    const payload = buildRetrievalRequest({ ...values, mode: "bm25", rerank_enabled: true }, "custom", pro);
    expect(payload.retrieval_options).toEqual({ mode: "bm25" });
    expect(payload.rerank_options).toEqual({ enabled: true, top_n: 5 });
  });
  it.each(["speed", "balanced", "quality"] as const)("sends only scope and profile for %s", (profile) => {
    const stale = { ...values, top_k: NaN, rerank_enabled: true, rewrite_enabled: true };
    expect(retrievalSchema(profile, pro).safeParse(stale).success).toBe(true);
    expect(buildRetrievalRequest(stale, profile, pro)).toEqual({
      kb_id: values.kb_ids[0], query: values.query, user_id: "debug_user", profile,
    });
  });
  it("chooses an allowed default and omits disabled custom options", () => {
    expect(selectProfile([])).toBeUndefined();
    expect(selectProfile(["speed"], "quality")).toBe("speed");
    expect(selectProfile(standard.allowed_profiles, "quality")).toBe("balanced");
    expect(selectProfile(pro.allowed_profiles, "quality")).toBe("quality");
    const stale = { ...values, rerank_enabled: true, rewrite_enabled: true, rerank_top_n: NaN };
    expect(retrievalSchema("custom", standard).safeParse(stale).success).toBe(true);
    const custom = buildRetrievalRequest(stale, "custom", { ...standard, hybrid_allowed: false });
    expect(custom.retrieval_options).toEqual({ mode: "vector" });
    expect(custom).not.toHaveProperty("query_options");
    expect(custom).not.toHaveProperty("rerank_options");
  });
  it("validates active parameters without blocking a hidden parameter", () => {
    const schema = retrievalSchema("custom", pro);
    expect(schema.safeParse({ ...values, vector_top_k: NaN }).success).toBe(false);
    expect(schema.safeParse({
      ...values, mode: "vector", vector_top_k: NaN, rerank_top_n: NaN,
    }).success).toBe(true);
    expect(schema.safeParse({ ...values, top_k: 0 }).success).toBe(false);
    expect(schema.safeParse({ ...values, kb_ids: ["invalid"] }).success).toBe(false);
  });
  it("renders zero as a score and roundtrips scope parameters", () => {
    expect(formatScore(0)).toBe("0.000000");
    expect(formatScore(null)).toBe("—");
    const url = scopeUrl("/retrieve", values.kb_ids[0]);
    expect(url).not.toContain("tenant_id");
    expect(readScope(new URL(url, "http://test").searchParams)).toEqual({
      kbId: values.kb_ids[0], valid: true,
    });
  });
});
