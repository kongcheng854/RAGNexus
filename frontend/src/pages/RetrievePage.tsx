import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { ChevronDown, CircleAlert, LoaderCircle, Search } from "lucide-react";
import { RetrievalFeedback } from "../components/RetrievalFeedback";
import { AppHeader } from "../components/AppHeader";
import { HelpTooltip } from "../components/HelpTooltip";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { ragService, RetrievalPlanError } from "../services/rag";
import { knowledgeBaseService } from "../services/knowledge";
import { errorMessage } from "../lib/axios";
import { useAuthMe } from "../hooks/useAuthMe";
import { readScope } from "../utils/navigation";
import type { RetrieveProfile } from "../types/api";
import { planLabels } from "../utils/tenant";
import { buildRetrievalRequest, formatScore, profileOptions, retrievalSchema, selectProfile, type RetrievalValues } from "../utils/retrieval";

const sourceColors = ["bg-emerald-100 text-emerald-900", "bg-sky-100 text-sky-900", "bg-amber-100 text-amber-900",
  "bg-violet-100 text-violet-900", "bg-rose-100 text-rose-900"];

function SourceBadge({ id, name, scope }: { id: string; name?: string | null; scope: string[] }) {
  const color = sourceColors[Math.max(0, scope.indexOf(id)) % sourceColors.length];
  return <span title={id} aria-label={`来源库：${name || id}`}
    className={`max-w-full break-words rounded-md px-2 py-1 text-xs font-medium ${color}`}>{name?.trim() || id.slice(0, 8)}</span>;
}

const parameterHelp = {
  vector_top_k: "从向量库取回多少个语义相似片段，作为混合检索的一路候选。",
  bm25_top_k: "从关键词索引取回多少个片段，适合专有名词、错误码等匹配。",
  rrf_k: "RRF 融合的平滑参数。值越大，各路排名之间的分数差异越小，通常保持 60。",
};

const scoreHelp: Record<string, string> = {
  score: "联查时为跨库 RRF 分数。单库按所选模式显示向量、BM25 或混合融合分数。",
  vector: "向量召回的原始相似度，分数越高通常越接近问题语义。",
  BM25: "关键词召回的原始相关性分数，适合在同一次关键词检索中比较。",
  rerank: "大模型对候选片段的相关性评分，范围为 0 到 1。",
  "vector rank": "该片段在向量召回中的原始排名，从 1 开始。缺失时显示横线。",
  "BM25 rank": "该片段在关键词召回中的原始排名，从 1 开始。缺失时显示横线。",
};

export function RetrievePage() {
  const auth = useAuthMe();
  const queryClient = useQueryClient();
  const [preferredProfile, setPreferredProfile] = useState<RetrieveProfile>();
  const features = auth.data?.features;
  const profile = selectProfile(features?.allowed_profiles ?? [], preferredProfile);
  const [params] = useSearchParams();
  const { kbId } = readScope(params);
  const tenantId = auth.data?.tenant_id;
  const tree = useQuery({
    queryKey: ["knowledge-tree", "", tenantId],
    queryFn: ({ signal }) => knowledgeBaseService.fetchTree(undefined, signal),
    enabled: !!tenantId,
  });
  const bases = tree.data?.find((tenant) => tenant.tenant_id === tenantId)?.knowledge_bases;
  const limit = auth.data?.limits.max_kb_per_retrieve;
  const form = useForm<RetrievalValues>({
    resolver: zodResolver(retrievalSchema(profile, features)),
    defaultValues: {
      kb_ids: [], query: "", top_k: 5, mode: "hybrid",
      vector_top_k: 20, bm25_top_k: 20, rrf_k: 60, rewrite_enabled: false, rerank_enabled: false, rerank_top_n: 5,
    },
  });
  useEffect(() => {
    form.setValue("kb_ids", kbId ? [kbId.toLowerCase()] : []);
  }, [kbId, tenantId, form]);
  useEffect(() => {
    if (!tree.isSuccess) return;
    const selected = form.getValues("kb_ids");
    const visible = selected.filter((id) => bases?.some((base) => base.kb_id === id));
    if (selected.length !== visible.length) form.setValue("kb_ids", visible);
  }, [bases, tree.isSuccess, kbId, tenantId, form]);
  const selected = form.watch("kb_ids");
  const overLimit = limit !== undefined && selected.length > limit;
  const canSearch = tree.isSuccess && selected.length > 0 && !overLimit &&
    selected.every((id) => bases?.some((base) => base.kb_id === id));
  useEffect(() => {
    if (features && !features.hybrid_allowed && form.getValues("mode") === "hybrid") form.setValue("mode", "vector");
  }, [features, form]);
  const mode = form.watch("mode");
  const rerankEnabled = form.watch("rerank_enabled");
  const request = useMutation({
    mutationFn: ragService.retrieve,
    retry: false,
    onError: (error) => {
      if (error instanceof RetrievalPlanError) void queryClient.invalidateQueries({ queryKey: ["auth-me"] });
    },
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["auth-me"] }); },
  });
  const result = request.isSuccess ? request.data : undefined;
  const metadata = result?.metadata;
  const processing = metadata?.query_processing;

  function issue(key: keyof RetrievalValues) {
    const message = form.formState.errors[key]?.message;
    return message ? <span role="alert" className="field-error mt-1">{message}</span> : null;
  }

  return (
    <div className="min-h-screen">
      <AppHeader />
      <main className="workspace-page">
        <div className="page-heading"><div><p className="eyebrow">RETRIEVAL DEBUGGER</p><h1>检索调试</h1><p className="mt-3 text-muted">用一次查询，检查召回片段与各阶段的评分。</p></div></div>
        <div className="space-y-6">
        <form id="retrieve-form" onSubmit={form.handleSubmit((values) => {
          if (canSearch && auth.isSuccess && profile && features) request.mutate(buildRetrievalRequest(values, profile, features));
        })}>
          <details open className="panel configuration">
            <summary className="panel-heading cursor-pointer list-none"><h2>检索配置</h2><ChevronDown className="configuration-chevron text-muted" size={18} /></summary>
            <fieldset disabled={request.isPending} className="space-y-6 p-6">
              <fieldset>
                <legend className="mb-3 text-sm font-semibold">检索预设</legend>
                <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                  {profileOptions.map(({ value, label, description, minimum }) => {
                    const allowed = features?.allowed_profiles.includes(value) ?? false;
                    const reason = !allowed && auth.data
                      ? `当前为${planLabels[auth.data.plan]}套餐，不支持「${label}」。如需使用，请升级到${minimum}套餐。` : undefined;
                    return (
                      <div key={value} title={reason} className={`flex items-center gap-2 rounded-lg border px-3 py-2.5 ${profile === value ? "border-primary bg-[#eef6f0]" : ""}`}>
                        <label className={`flex flex-1 items-center gap-2 text-sm ${allowed ? "cursor-pointer" : "cursor-not-allowed text-muted"}`}>
                          <input type="radio" name="retrieve-profile" value={value} checked={profile === value}
                            disabled={!allowed} onChange={() => setPreferredProfile(value)} className="accent-primary" />
                          <span>{label}<small className="block text-xs text-muted">{description}</small></span>
                        </label>
                        {reason && <HelpTooltip label={label}>{reason}</HelpTooltip>}
                      </div>
                    );
                  })}
                </div>
              </fieldset>
              <fieldset>
                <legend className="mb-3 text-sm font-semibold">检索范围</legend>
                {tree.isLoading && <p role="status" className="text-sm text-muted">正在读取知识库…</p>}
                {tree.isError && <p role="alert" className="error-notice">{errorMessage(tree.error)} <button type="button"
                  className="underline" onClick={() => void tree.refetch()}>重试</button></p>}
                {tree.isSuccess && !bases?.length && <p className="text-sm text-muted">当前租户暂无知识库。</p>}
                <div className="grid gap-2 sm:grid-cols-2">
                  {bases?.map((base) => <label key={base.kb_id}
                    className={`flex min-w-0 cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 ${selected.includes(base.kb_id) ? "border-primary bg-[#eef6f0]" : ""}`}>
                    <input type="checkbox" className="h-4 w-4 shrink-0 accent-primary" checked={selected.includes(base.kb_id)}
                      onChange={(event) => {
                        form.setValue("kb_ids", event.target.checked ? [...selected, base.kb_id] : selected.filter((id) => id !== base.kb_id),
                          { shouldDirty: true, shouldValidate: true });
                        if (request.isError) request.reset();
                      }} />
                    <span className="min-w-0 break-words text-sm">{base.name}<small className="block break-all text-xs text-muted">{base.kb_id}</small></span>
                  </label>)}
                </div>
                {tree.isSuccess && !selected.length && <p className="mt-2 text-sm text-muted">请选择至少一个知识库</p>}
                {limit !== undefined && (overLimit || request.error instanceof RetrievalPlanError) &&
                  <p role="alert" className="mt-2 text-sm text-amber-800">当前套餐最多支持 {limit} 个库联合检索</p>}
              </fieldset>
              {profile === "custom" && <>
              <p className="border-b pb-3 text-sm font-medium text-muted">检索参数</p>
              <div className="debug-field"><div className="parameter-label"><label htmlFor="debug-top-k">top_k</label><HelpTooltip label="top_k">本次召回后保留多少个候选片段。开启重排时，这些片段会再被筛选排序。</HelpTooltip></div><div className="max-w-40"><Input id="debug-top-k" type="number" min={1} max={1000} {...form.register("top_k", { valueAsNumber: true })} />{issue("top_k")}</div></div>
              <div className="debug-field"><div className="parameter-label"><span id="mode-label">检索模式</span><HelpTooltip label="检索模式">vector 按语义相似度查找；bm25 按关键词查找；hybrid 合并两路结果并按排名融合。</HelpTooltip></div><div role="radiogroup" aria-labelledby="mode-label" className="flex flex-wrap gap-2">{(["vector", "bm25", "hybrid"] as const).filter((choice) => choice !== "hybrid" || features?.hybrid_allowed).map((choice) => <label key={choice} className={`mode-option ${mode === choice ? "mode-selected" : ""}`}><input type="radio" value={choice} {...form.register("mode")} className="sr-only" />{choice}</label>)}</div></div>
              {mode === "hybrid" && <div className="hybrid-fields">{(["vector_top_k", "bm25_top_k", "rrf_k"] as const).map((field) => <div key={field} className="debug-field"><div className="parameter-label"><label htmlFor={field}>{field}</label><HelpTooltip label={field}>{parameterHelp[field]}</HelpTooltip></div><div><Input id={field} type="number" min={1} max={field === "rrf_k" ? undefined : 1000} {...form.register(field, { valueAsNumber: true })} />{issue(field)}</div></div>)}</div>}
              <div className="debug-field">
                <div className="parameter-label"><label htmlFor="rewrite">启用 query 改写</label>
                  <HelpTooltip label="query 改写">用 AI 把口语问题改成更好搜的说法；更慢、消耗 LLM，可对比开关效果。</HelpTooltip>
                </div>
                <div><input id="rewrite" type="checkbox" className="h-4 w-4 accent-primary" disabled={!features?.query_rewrite_allowed} {...form.register("rewrite_enabled")} />
                  <p className="mt-1 text-xs text-muted">{features?.query_rewrite_allowed ? "未勾选时使用服务端默认配置。" : "专业套餐支持问题改写。"}</p>
                </div>
              </div>
              <div className="debug-field"><div className="parameter-label"><span>rerank</span><HelpTooltip label="rerank">让大模型再次比较候选片段与问题的相关性，精排后按 top_n 返回。会增加调用耗时。</HelpTooltip></div><div><label className="inline-flex cursor-pointer items-center gap-2 text-sm"><input type="checkbox" className="h-4 w-4 accent-primary" disabled={!features?.rerank_allowed} {...form.register("rerank_enabled")} />启用大模型重排</label>{!rerankEnabled && <p className="mt-1 text-xs text-muted">{features?.rerank_allowed ? "未勾选时使用服务端默认配置。" : "专业套餐支持重排。"}</p>}</div></div>
              {features?.rerank_allowed && rerankEnabled && <div className="debug-field"><label htmlFor="rerank-top-n">重排 top_n</label><div className="max-w-40"><Input id="rerank-top-n" type="number" min={1} max={1000} {...form.register("rerank_top_n", { valueAsNumber: true })} />{issue("rerank_top_n")}</div></div>}
              </>}
            </fieldset>
          </details>
        </form>
          <section className="panel">
            <div className="panel-heading"><h2>检索与召回</h2></div>
            <div className="space-y-5 p-6">
              <div className="debug-field items-start"><label htmlFor="debug-query" className="pt-3">query</label><div><textarea form="retrieve-form" id="debug-query" {...form.register("query")} rows={3} disabled={request.isPending} placeholder="例如：ZX999 错误码如何处理？" className="w-full resize-y rounded-lg border p-3 outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" />{issue("query")}</div></div>
              <div className="flex justify-end"><Button type="submit" form="retrieve-form" disabled={request.isPending || !auth.isSuccess || !profile || !canSearch}>{request.isPending ? <LoaderCircle size={17} className="animate-spin" /> : <Search size={17} />}{request.isPending ? "正在检索" : "检索"}</Button></div>
              {request.isError && <p role="alert" className="error-notice">{errorMessage(request.error)}</p>}
              {request.isPending && <p role="status" className="py-5 text-center text-sm text-muted">正在召回并处理候选片段，请稍候…</p>}
              {result && <div className="border-t pt-5" aria-live="polite">
                {result.metadata.trace_id
                  ? <RetrievalFeedback key={result.metadata.log_id} logId={result.metadata.log_id} traceId={result.metadata.trace_id} />
                  : <p className="mb-4 text-sm text-muted">Langfuse 未启用或本次追踪不可用。</p>}
                <div className="mb-5 flex flex-wrap justify-between gap-3"><h3 className="font-semibold">召回结果 <span className="ml-2 text-sm font-normal text-muted">{result.retrieved_chunks.length} 个片段</span></h3><p className="text-sm text-muted">查询 · {result.query}</p></div>
                {!result.retrieved_chunks.length && <p className="page-empty">未召回相关片段，请检查知识库与查询内容。</p>}
                <div className="space-y-4">{result.retrieved_chunks.map((chunk, index) => <article key={chunk.chunk_id} className="result-card">
                  <div className="mb-4 flex flex-wrap items-center gap-3"><span className="result-rank">#{index + 1}</span><h4 className="min-w-0 break-words font-semibold">{chunk.title}</h4><SourceBadge id={chunk.kb_id || result.kb_id} name={chunk.kb_name} scope={result.kb_ids ?? [result.kb_id]} />{chunk.retrieval_source && <span className="rounded-md bg-[#edf4ef] px-2 py-1 text-xs text-primary">{chunk.retrieval_source}</span>}</div>
                  <dl className="score-grid">{[
                    ["score", formatScore(chunk.score)], ["vector", formatScore(chunk.vector_score)],
                    ["BM25", formatScore(chunk.bm25_score)], ["rerank", formatScore(chunk.rerank_score)],
                    ["vector rank", chunk.vector_rank ?? "—"], ["BM25 rank", chunk.bm25_rank ?? "—"],
                  ].map(([label, value]) => <div key={label}><dt className="inline-flex items-center gap-1">{label}<HelpTooltip label={String(label)}>{scoreHelp[String(label)]}</HelpTooltip></dt><dd>{value}</dd></div>)}</dl>
                  <dl className="mt-4 space-y-1 text-xs text-muted"><div className="resource-id"><dt>document_id</dt><dd>{chunk.document_id}</dd></div><div className="resource-id"><dt>chunk_id</dt><dd>{chunk.chunk_id}</dd></div></dl>
                  {chunk.metadata?.heading_path?.trim() && chunk.metadata.chunk_type?.trim() && (
                    <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-1 break-words text-sm text-muted">
                      <div className="min-w-0"><dt className="inline">所属章节：</dt><dd className="inline">{chunk.metadata.heading_path.split("/").join(" / ")}</dd></div>
                      <div><dt className="inline">块类型：</dt><dd className="inline">{chunk.metadata.chunk_type}</dd></div>
                    </dl>
                  )}
                  <details className="mt-4 border-t pt-3" open={index === 0}><summary className="cursor-pointer text-sm font-medium text-primary">片段内容</summary><p className="mt-3 whitespace-pre-wrap break-words text-sm leading-7">{chunk.content}</p></details>
                </article>)}</div>
              </div>}
            </div>
          </section>
        </div>
        <section className="panel mt-6">
          <div className="panel-heading"><h2>运行摘要</h2><span className="text-xs text-muted">服务端 metadata</span></div>
          {metadata ? <div className="p-6">
            <dl className="mb-4 space-y-1 text-xs text-muted">
              <div className="resource-id"><dt>log_id</dt><dd>{metadata.log_id}</dd></div>
              {metadata.trace_id && <div className="resource-id"><dt>trace_id</dt><dd>{metadata.trace_id}</dd></div>}
            </dl>
            <dl className="summary-grid">
              <div><dt>检索模式</dt><dd>{metadata.tenant_policy.effective_mode}</dd></div>
              <div><dt>top_k</dt><dd>{metadata.top_k}</dd></div>
              <div><dt>检索范围</dt><dd>{metadata.retrieval?.multi_kb ? `联查 ${metadata.retrieval.kb_count ?? result?.kb_ids?.length} 个库` : "单库"}</dd></div>
              {metadata.retrieval?.fusion && <div><dt>融合方式</dt><dd>{metadata.retrieval.fusion}</dd></div>}
              <div><dt>服务端耗时</dt><dd>{metadata.latency_ms} ms</dd></div>
              <div><dt>向量召回</dt><dd>{metadata.retrieval?.vector_count ?? "—"}</dd></div>
              <div><dt>BM25 召回</dt><dd>{metadata.retrieval?.bm25_count ?? "—"}</dd></div>
              <div><dt>融合候选</dt><dd>{metadata.retrieval?.fused_count ?? "—"}</dd></div>
            </dl>
            <details className="mt-4 text-sm">
              <summary className="cursor-pointer text-primary">本次生效的套餐与预设</summary>
              <dl className="summary-grid mt-3">
                <div><dt>套餐</dt><dd>{planLabels[metadata.tenant_policy.plan]}</dd></div>
                <div><dt>检索预设</dt><dd>{metadata.tenant_policy.retrieve_profile}</dd></div>
                <div><dt>问题改写</dt><dd>{metadata.tenant_policy.effective_query_rewrite ? "开启" : "关闭"}</dd></div>
                <div><dt>重排</dt><dd>{metadata.tenant_policy.effective_rerank ? "开启" : "关闭"}</dd></div>
              </dl>
            </details>
            <dl className="summary-grid mt-5 break-words">
              <div><dt>原话</dt><dd>{result?.query}</dd></div>
              {processing && <>
                <div><dt>实际检索句</dt><dd>{processing.effective_query}</dd></div>
                {processing.search_query !== processing.effective_query &&
                  <div><dt>最终检索句</dt><dd className="text-primary">{processing.search_query}</dd></div>}
                {processing.strategy === "rewrite" &&
                  <div><dt>改写耗时</dt><dd>{processing.rewrite_latency_ms} ms</dd></div>}
                {processing.synonym_applied &&
                  <div><dt>追加的扩展词</dt><dd>{processing.synonym_expansions.join("、")}</dd></div>}
              </>}
            </dl>
            {processing?.degraded && <p role="status" className="degraded-notice">
              <CircleAlert size={16} />改写失败，已用原话检索 · {processing.degraded_reason}
            </p>}
            <p className="mt-5 text-sm text-muted">rerank · {metadata.rerank?.enabled ? `${metadata.rerank.provider} / ${metadata.rerank.model || "—"}` : "未启用"}</p>
            {metadata.retrieval?.degraded && <p role="status" className="degraded-notice"><CircleAlert size={16} />hybrid 已降级 · {metadata.retrieval.degraded_reason || "关键词召回不可用"}</p>}
            {metadata.rerank?.degraded && <p role="status" className="degraded-notice"><CircleAlert size={16} />rerank 已降级 · {metadata.rerank.error || "重排不可用"}</p>}
          </div> : <p className="px-6 py-8 text-sm text-muted">完成一次检索后，查看服务端耗时和召回阶段信息。</p>}
        </section>
      </main>
    </div>
  );
}
