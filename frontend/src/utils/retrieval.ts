import { z } from "zod";
import type { AuthMeData, RetrieveProfile } from "../types/api";
import type { RetrievalRequest } from "../types/retrieval";

const count = z.number({ invalid_type_error: "请输入整数" }).int("请输入整数").min(1, "至少为 1").max(1000, "最多为 1000");
type Features = AuthMeData["features"];

export const profileOptions = [
  { value: "speed", label: "追求速度", description: "向量检索 · 3 个候选", minimum: "免费" },
  { value: "balanced", label: "均衡", description: "混合检索 · 5 个候选", minimum: "标准" },
  { value: "quality", label: "追求质量", description: "混合检索 · 改写与重排", minimum: "专业" },
  { value: "custom", label: "自定义", description: "手动配置检索参数", minimum: "标准" },
] as const;

export function selectProfile(allowed: readonly RetrieveProfile[], preferred?: RetrieveProfile): RetrieveProfile | undefined {
  if (preferred && allowed.includes(preferred)) return preferred;
  return allowed.includes("balanced") ? "balanced" : allowed[0];
}

const fields = z.object({
  kb_ids: z.array(z.string().uuid("无效的知识库 UUID")).min(1, "请选择至少一个知识库"),
  query: z.string().trim().min(1, "请输入检索问题").max(2000, "问题最多 2000 个字符").refine((v) => !v.includes("\u0000"), "包含无效字符"),
  top_k: z.number().or(z.nan()),
  mode: z.enum(["vector", "bm25", "hybrid"]),
  vector_top_k: z.number().or(z.nan()),
  bm25_top_k: z.number().or(z.nan()),
  rrf_k: z.number().or(z.nan()),
  rewrite_enabled: z.boolean(),
  rerank_enabled: z.boolean(),
  rerank_top_n: z.number().or(z.nan()),
});

export function retrievalSchema(profile?: RetrieveProfile, features?: Features) {
  return fields.superRefine((values, context) => {
    if (profile !== "custom") return;
    if (!count.safeParse(values.top_k).success) {
      context.addIssue({ code: z.ZodIssueCode.custom, path: ["top_k"], message: "请输入 1–1000 的整数" });
    }
    if (values.mode === "hybrid" && features?.hybrid_allowed) {
      for (const field of ["vector_top_k", "bm25_top_k", "rrf_k"] as const) {
        const validator = field === "rrf_k" ? z.number().int().min(1) : count;
        if (!validator.safeParse(values[field]).success) {
          context.addIssue({ code: z.ZodIssueCode.custom, path: [field], message: "请输入有效的正整数" });
        }
      }
    }
    if (features?.rerank_allowed && values.rerank_enabled && !count.safeParse(values.rerank_top_n).success) {
      context.addIssue({ code: z.ZodIssueCode.custom, path: ["rerank_top_n"], message: "请输入 1–1000 的整数" });
    }
  });
}
export type RetrievalValues = z.infer<typeof fields>;

export function buildRetrievalRequest(values: RetrievalValues, profile: RetrieveProfile, features: Features): RetrievalRequest {
  const scope = values.kb_ids.length === 1 ? { kb_id: values.kb_ids[0] } : { kb_ids: values.kb_ids };
  if (profile !== "custom") {
    return { profile, ...scope, query: values.query, user_id: "debug_user" };
  }
  const mode = values.mode === "hybrid" && !features.hybrid_allowed ? "vector" : values.mode;
  return {
    profile,
    ...scope,
    query: values.query, user_id: "debug_user", top_k: values.top_k,
    retrieval_options: {
      mode,
      ...(mode === "hybrid" ? {
        vector_top_k: values.vector_top_k, bm25_top_k: values.bm25_top_k, rrf_k: values.rrf_k,
      } : {}),
    },
    ...(features.query_rewrite_allowed && values.rewrite_enabled ? { query_options: { enabled: true, strategy: "rewrite" as const } } : {}),
    ...(features.rerank_allowed && values.rerank_enabled ? { rerank_options: { enabled: true, top_n: values.rerank_top_n } } : {}),
  };
}

export function formatScore(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(6) : "—";
}
