import { z } from "zod";
import type { RetrievalRequest } from "../types/retrieval";

const count = z.number({ invalid_type_error: "请输入整数" }).int("请输入整数").min(1, "至少为 1").max(1000, "最多为 1000");
export const retrievalSchema = z.object({
  kb_id: z.string().trim().uuid("请填写有效的知识库 UUID"),
  query: z.string().trim().min(1, "请输入检索问题").max(2000, "问题最多 2000 个字符").refine((v) => !v.includes("\u0000"), "包含无效字符"),
  top_k: count,
  mode: z.enum(["vector", "bm25", "hybrid"]),
  vector_top_k: z.number().or(z.nan()),
  bm25_top_k: z.number().or(z.nan()),
  rrf_k: z.number().or(z.nan()),
  rerank_enabled: z.boolean(),
  rerank_top_n: z.number().or(z.nan()),
}).superRefine((values, context) => {
  if (values.mode === "hybrid") {
    for (const field of ["vector_top_k", "bm25_top_k", "rrf_k"] as const) {
      const validator = field === "rrf_k" ? z.number().int().min(1) : count;
      if (!validator.safeParse(values[field]).success) {
        context.addIssue({ code: z.ZodIssueCode.custom, path: [field], message: "请输入有效的正整数" });
      }
    }
  }
  if (values.rerank_enabled && !count.safeParse(values.rerank_top_n).success) {
    context.addIssue({ code: z.ZodIssueCode.custom, path: ["rerank_top_n"], message: "请输入 1–1000 的整数" });
  }
});
export type RetrievalValues = z.infer<typeof retrievalSchema>;

export function buildRetrievalRequest(values: RetrievalValues): RetrievalRequest {
  return {
    kb_id: values.kb_id,
    query: values.query, user_id: "debug_user", top_k: values.top_k,
    retrieval_options: {
      mode: values.mode,
      ...(values.mode === "hybrid" ? {
        vector_top_k: values.vector_top_k, bm25_top_k: values.bm25_top_k, rrf_k: values.rrf_k,
      } : {}),
    },
    ...(values.rerank_enabled ? { rerank_options: { enabled: true, top_n: values.rerank_top_n } } : {}),
  };
}

export function formatScore(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(6) : "—";
}
