import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { ChevronDown, CircleAlert, LoaderCircle, Search } from "lucide-react";
import { AppHeader } from "../components/AppHeader";
import { HelpTooltip } from "../components/HelpTooltip";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { ragService } from "../services/rag";
import { errorMessage } from "../lib/axios";
import { readScope } from "../utils/navigation";
import { buildRetrievalRequest, formatScore, retrievalSchema, type RetrievalValues } from "../utils/retrieval";

const parameterHelp = {
  vector_top_k: "从向量库取回多少个语义相似片段，作为混合检索的一路候选。",
  bm25_top_k: "从关键词索引取回多少个片段，适合专有名词、错误码等匹配。",
  rrf_k: "RRF 融合的平滑参数。值越大，各路排名之间的分数差异越小，通常保持 60。",
};

const scoreHelp: Record<string, string> = {
  score: "当前召回排序分数。正常 hybrid 模式使用 RRF 融合分数，降级时使用向量分数。",
  vector: "向量召回的原始相似度，分数越高通常越接近问题语义。",
  BM25: "关键词召回的原始相关性分数，适合在同一次关键词检索中比较。",
  rerank: "大模型对候选片段的相关性评分，范围为 0 到 1。",
  "vector rank": "该片段在向量召回中的原始排名，从 1 开始。缺失时显示横线。",
  "BM25 rank": "该片段在关键词召回中的原始排名，从 1 开始。缺失时显示横线。",
};

export function RetrievePage() {
  const [params] = useSearchParams();
  const { kbId } = readScope(params);
  const form = useForm<RetrievalValues>({
    resolver: zodResolver(retrievalSchema),
    defaultValues: {
      kb_id: kbId, query: "", top_k: 5, mode: "hybrid",
      vector_top_k: 20, bm25_top_k: 20, rrf_k: 60, rerank_enabled: false, rerank_top_n: 5,
    },
  });
  useEffect(() => {
    form.setValue("kb_id", kbId);
  }, [kbId, form]);
  const mode = form.watch("mode");
  const rerankEnabled = form.watch("rerank_enabled");
  const request = useMutation({ mutationFn: ragService.retrieve, retry: false });
  const result = request.isSuccess ? request.data : undefined;
  const metadata = result?.metadata;

  function issue(key: keyof RetrievalValues) {
    const message = form.formState.errors[key]?.message;
    return message ? <span role="alert" className="field-error mt-1">{message}</span> : null;
  }

  return (
    <div className="min-h-screen">
      <AppHeader />
      <main className="workspace-page">
        <div className="page-heading"><div><p className="eyebrow">RETRIEVAL DEBUGGER</p><h1>检索调试</h1><p className="mt-3 text-muted">用一次查询，检查召回片段与各阶段的评分。</p></div></div>
        <form onSubmit={form.handleSubmit((values) => request.mutate(buildRetrievalRequest(values)))} className="space-y-6">
          <details open className="panel configuration">
            <summary className="panel-heading cursor-pointer list-none"><h2>检索配置</h2><ChevronDown className="configuration-chevron text-muted" size={18} /></summary>
            <fieldset disabled={request.isPending} className="space-y-6 p-6">
              <div className="scope-fields single-scope">
                <div className="debug-field"><label htmlFor="debug-kb">知识库 ID</label><div><Input id="debug-kb" {...form.register("kb_id")} placeholder="知识库 UUID" />{issue("kb_id")}</div></div>
              </div>
              <p className="border-b pb-3 text-sm font-medium text-muted">检索参数</p>
              <div className="debug-field"><div className="parameter-label"><label htmlFor="debug-top-k">top_k</label><HelpTooltip label="top_k">本次召回后保留多少个候选片段。开启重排时，这些片段会再被筛选排序。</HelpTooltip></div><div className="max-w-40"><Input id="debug-top-k" type="number" min={1} max={1000} {...form.register("top_k", { valueAsNumber: true })} />{issue("top_k")}</div></div>
              <div className="debug-field"><div className="parameter-label"><span id="mode-label">检索模式</span><HelpTooltip label="检索模式">vector 按语义相似度查找；bm25 按关键词查找；hybrid 合并两路结果并按排名融合。</HelpTooltip></div><div role="radiogroup" aria-labelledby="mode-label" className="flex flex-wrap gap-2">{(["vector", "bm25", "hybrid"] as const).map((choice) => <label key={choice} className={`mode-option ${mode === choice ? "mode-selected" : ""}`}><input type="radio" value={choice} {...form.register("mode")} className="sr-only" />{choice}</label>)}</div></div>
              {mode === "hybrid" && <div className="hybrid-fields">{(["vector_top_k", "bm25_top_k", "rrf_k"] as const).map((field) => <div key={field} className="debug-field"><div className="parameter-label"><label htmlFor={field}>{field}</label><HelpTooltip label={field}>{parameterHelp[field]}</HelpTooltip></div><div><Input id={field} type="number" min={1} max={field === "rrf_k" ? undefined : 1000} {...form.register(field, { valueAsNumber: true })} />{issue(field)}</div></div>)}</div>}
              <div className="debug-field"><div className="parameter-label"><span>rerank</span><HelpTooltip label="rerank">让大模型再次比较候选片段与问题的相关性，精排后按 top_n 返回。会增加调用耗时。</HelpTooltip></div><div><label className="inline-flex cursor-pointer items-center gap-2 text-sm"><input type="checkbox" className="h-4 w-4 accent-primary" {...form.register("rerank_enabled")} />启用大模型重排</label>{!rerankEnabled && <p className="mt-1 text-xs text-muted">未勾选时使用服务端默认配置。</p>}</div></div>
              {rerankEnabled && <div className="debug-field"><label htmlFor="rerank-top-n">重排 top_n</label><div className="max-w-40"><Input id="rerank-top-n" type="number" min={1} max={1000} {...form.register("rerank_top_n", { valueAsNumber: true })} />{issue("rerank_top_n")}</div></div>}
            </fieldset>
          </details>
          <section className="panel">
            <div className="panel-heading"><h2>检索与召回</h2></div>
            <div className="space-y-5 p-6">
              <div className="debug-field items-start"><label htmlFor="debug-query" className="pt-3">query</label><div><textarea id="debug-query" {...form.register("query")} rows={3} disabled={request.isPending} placeholder="例如：ZX999 错误码如何处理？" className="w-full resize-y rounded-lg border p-3 outline-none focus:border-primary focus:ring-2 focus:ring-primary/10" />{issue("query")}</div></div>
              <div className="flex justify-end"><Button type="submit" disabled={request.isPending}>{request.isPending ? <LoaderCircle size={17} className="animate-spin" /> : <Search size={17} />}{request.isPending ? "正在检索" : "检索"}</Button></div>
              {request.isError && <p role="alert" className="error-notice">{errorMessage(request.error)}</p>}
              {request.isPending && <p role="status" className="py-5 text-center text-sm text-muted">正在召回并处理候选片段，请稍候…</p>}
              {result && <div className="border-t pt-5" aria-live="polite"><div className="mb-5 flex flex-wrap justify-between gap-3"><h3 className="font-semibold">召回结果 <span className="ml-2 text-sm font-normal text-muted">{result.retrieved_chunks.length} 个片段</span></h3><p className="text-sm text-muted">查询 · {result.query}</p></div>
                {!result.retrieved_chunks.length && <p className="page-empty">未召回相关片段，请检查知识库与查询内容。</p>}
                <div className="space-y-4">{result.retrieved_chunks.map((chunk, index) => <article key={chunk.chunk_id} className="result-card">
                  <div className="mb-4 flex flex-wrap items-center gap-3"><span className="result-rank">#{index + 1}</span><h4 className="min-w-0 break-words font-semibold">{chunk.title}</h4>{chunk.retrieval_source && <span className="rounded-md bg-[#edf4ef] px-2 py-1 text-xs text-primary">{chunk.retrieval_source}</span>}</div>
                  <dl className="score-grid">{[
                    ["score", formatScore(chunk.score)], ["vector", formatScore(chunk.vector_score)],
                    ["BM25", formatScore(chunk.bm25_score)], ["rerank", formatScore(chunk.rerank_score)],
                    ["vector rank", chunk.vector_rank ?? "—"], ["BM25 rank", chunk.bm25_rank ?? "—"],
                  ].map(([label, value]) => <div key={label}><dt className="inline-flex items-center gap-1">{label}<HelpTooltip label={String(label)}>{scoreHelp[String(label)]}</HelpTooltip></dt><dd>{value}</dd></div>)}</dl>
                  <dl className="mt-4 space-y-1 text-xs text-muted"><div className="resource-id"><dt>document_id</dt><dd>{chunk.document_id}</dd></div><div className="resource-id"><dt>chunk_id</dt><dd>{chunk.chunk_id}</dd></div></dl>
                  <details className="mt-4 border-t pt-3" open={index === 0}><summary className="cursor-pointer text-sm font-medium text-primary">片段内容</summary><p className="mt-3 whitespace-pre-wrap break-words text-sm leading-7">{chunk.content}</p></details>
                </article>)}</div>
              </div>}
            </div>
          </section>
        </form>
        <section className="panel mt-6">
          <div className="panel-heading"><h2>运行摘要</h2><span className="text-xs text-muted">服务端 metadata</span></div>
          {metadata ? <div className="p-6">
            <dl className="summary-grid">
              <div><dt>检索模式</dt><dd>{metadata.retrieval?.mode ?? "—"}</dd></div>
              <div><dt>top_k</dt><dd>{metadata.top_k}</dd></div>
              <div><dt>服务端耗时</dt><dd>{metadata.latency_ms} ms</dd></div>
              <div><dt>向量召回</dt><dd>{metadata.retrieval?.vector_count ?? "—"}</dd></div>
              <div><dt>BM25 召回</dt><dd>{metadata.retrieval?.bm25_count ?? "—"}</dd></div>
              <div><dt>融合候选</dt><dd>{metadata.retrieval?.fused_count ?? "—"}</dd></div>
            </dl>
            <p className="mt-5 text-sm text-muted">rerank · {metadata.rerank?.enabled ? `${metadata.rerank.provider} / ${metadata.rerank.model || "—"}` : "未启用"}</p>
            {metadata.retrieval?.degraded && <p role="status" className="degraded-notice"><CircleAlert size={16} />hybrid 已降级 · {metadata.retrieval.degraded_reason || "关键词召回不可用"}</p>}
            {metadata.rerank?.degraded && <p role="status" className="degraded-notice"><CircleAlert size={16} />rerank 已降级 · {metadata.rerank.error || "重排不可用"}</p>}
          </div> : <p className="px-6 py-8 text-sm text-muted">完成一次检索后，查看服务端耗时和召回阶段信息。</p>}
        </section>
      </main>
    </div>
  );
}
