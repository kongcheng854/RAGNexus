import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, ChevronDown, ChevronRight, Copy, FileText, LoaderCircle, RefreshCw, Search } from "lucide-react";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { EditKnowledgeBaseDialog } from "../components/EditKnowledgeBaseDialog";
import { StatusBadge } from "../components/StatusBadge";
import { useKnowledgeBases } from "../hooks/useKnowledgeBases";
import { deleteDocument, reindexDocument } from "../services/documentService";
import type { TenantKnowledgeTree } from "../types/retrieval";
import { AppHeader } from "../components/AppHeader";
import { TenantBadges } from "../components/TenantBadges";
import { Button, buttonVariants } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { knowledgeBaseService } from "../services/knowledge";
import { errorMessage } from "../lib/http";
import { scopeUrl } from "../utils/navigation";

type DeleteTarget = { kind: "knowledge-base" | "document"; id: string; name: string; tenantId: string };

function treePollInterval(tenants?: TenantKnowledgeTree[]) {
  return tenants?.some((tenant) =>
    tenant.knowledge_bases.some((base) => base.documents.some((doc) => doc.status === 3))
  ) ? 3000 : false;
}

export function KnowledgeFileTreePage() {
  const client = useQueryClient();
  const { updateShortcut, storageWarning } = useKnowledgeBases();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [target, setTarget] = useState<DeleteTarget | null>(null);
  const [keyword, setKeyword] = useState("");
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState(new Set<string>());
  const [copyNotice, setCopyNotice] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setFilter(keyword.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [keyword]);
  const query = useQuery<TenantKnowledgeTree[]>({
    queryKey: ["knowledge-tree", filter],
    refetchInterval: (current) => treePollInterval(current.state.data),
    queryFn: ({ signal }) => knowledgeBaseService.fetchTree(filter, signal),
  });
  const bases = query.data?.flatMap((tenant) => tenant.knowledge_bases.map((base) => ({
    ...base, tenantId: tenant.tenant_id,
  }))) ?? [];
  const refreshTree = () => client.invalidateQueries({ queryKey: ["knowledge-tree"] });
  const retry = useMutation({
    mutationFn: reindexDocument,
    onSuccess: refreshTree,
    onError: refreshTree,
  });
  const remove = useMutation({
    mutationFn: (item: DeleteTarget) => item.kind === "knowledge-base"
      ? knowledgeBaseService.deleteKnowledgeBase(item.id) : deleteDocument(item.id),
    onSuccess: async (_, item) => {
      if (item.kind === "knowledge-base") updateShortcut(item.tenantId, item.id);
      await refreshTree();
      setTarget(null);
    },
    onError: refreshTree,
  });

  function confirmDelete(item: DeleteTarget) {
    remove.reset();
    setTarget(item);
  }

  function toggle(key: string) {
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  async function copyId(id: string) {
    try {
      await navigator.clipboard.writeText(id);
      setCopyNotice(`已复制知识库 ID：${id}`);
    } catch {
      setCopyNotice("复制失败，请选中知识库 ID 手动复制。");
    }
  }

  return (
    <div className="min-h-screen">
      <AppHeader />
      <main className="workspace-page">
        <div className="page-heading">
          <div><p className="eyebrow">KNOWLEDGE LIBRARY</p><h1>知识库列表</h1><p className="mt-3 text-muted">管理当前租户的知识库，查看文档索引进度与失败原因。</p></div>
          <div className="flex flex-wrap items-center gap-4"><TenantBadges showPlan /><Button variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw size={16} className={query.isFetching ? "animate-spin" : ""} />刷新</Button></div>
        </div>
        <section className="panel">
          <div className="flex flex-wrap items-center justify-between gap-4 p-6">
            <label className="relative block w-full max-w-md"><Search className="pointer-events-none absolute left-3 top-3 text-muted" size={18} /><Input aria-label="搜索知识库名称" maxLength={128} placeholder="搜索知识库名称" value={keyword} onChange={(event) => setKeyword(event.target.value)} className="pl-10" /></label>
            <span className="text-sm text-muted">{treePollInterval(query.data) ? "有文档正在索引…" : `${bases.length} 个知识库`}</span>
          </div>
          {retry.isError && <p role="alert" className="error-notice mx-6 mb-4">{errorMessage(retry.error)}</p>}
          {storageWarning && <p role="alert" className="error-notice mx-6 mb-4">{storageWarning}</p>}
          {copyNotice && <p role="status" className="mx-6 mb-4 break-all text-sm text-primary">{copyNotice}</p>}
          {query.isError ? <div className="p-6 pt-0"><p role="alert" className="error-notice">{errorMessage(query.error)}</p></div>
            : query.isPending ? <div className="page-empty"><LoaderCircle size={24} className="animate-spin" /><p>正在读取知识库…</p></div>
              : !bases.length ? <div className="page-empty"><BookOpen size={30} strokeWidth={1.4} /><p>{filter ? "没有匹配的知识库" : "还没有知识库"}</p><Link to="/" className={buttonVariants({ variant: "outline", size: "sm" })}>去创建知识库</Link></div>
                : <Table className="min-w-[900px]">
                  <TableHeader><TableRow><TableHead className="w-[27%]">知识库 / 文档</TableHead><TableHead>kb_id/document_id</TableHead><TableHead className="whitespace-nowrap">片段数</TableHead><TableHead>索引状态</TableHead><TableHead>操作</TableHead></TableRow></TableHeader>
                  <TableBody>
                    {bases.map((base) => <Fragment key={base.kb_id}>
                      <TableRow>
                        <TableCell><button className="tree-toggle" aria-expanded={expanded.has(base.kb_id)} onClick={() => toggle(base.kb_id)}>{expanded.has(base.kb_id) ? <ChevronDown size={16} /> : <ChevronRight size={16} />}<BookOpen size={17} className="shrink-0 text-primary" /><span className="break-words font-medium">{base.name}</span></button>{base.description && <p className="ml-10 mt-2 max-w-sm break-words text-xs text-muted">{base.description}</p>}</TableCell>
                        <TableCell><code className="break-all text-xs">{base.kb_id}</code></TableCell>
                        <TableCell className="text-muted">{base.documents.reduce((sum, doc) => sum + doc.chunk_count, 0)}</TableCell>
                        <TableCell />
                        <TableCell><div className="flex flex-wrap items-center gap-1"><Button variant="ghost" size="icon" onClick={() => void copyId(base.kb_id)} aria-label={`复制 ${base.name} 的知识库 ID`}><Copy size={15} /></Button><Link className={buttonVariants({ variant: "ghost", size: "sm" })} to={scopeUrl("/", base.kb_id)}>去上传</Link><Link className={buttonVariants({ variant: "outline", size: "sm" })} to={scopeUrl("/retrieve", base.kb_id)}>去检索</Link>
                          <Button variant="ghost" size="sm" onClick={() => setEditingId(base.kb_id)}>编辑</Button>
                          <Button variant="ghost" size="sm" className="text-[#a23426]"
                            disabled={base.documents.some((doc) => doc.status === 3)}
                            onClick={() => confirmDelete({ kind: "knowledge-base", id: base.kb_id, name: base.name, tenantId: base.tenantId })}>删除库</Button>
                        </div></TableCell>
                      </TableRow>
                      {expanded.has(base.kb_id) && (base.documents.length ? base.documents.map((doc) => (
                        <TableRow key={doc.document_id} className="bg-[#fcfdfc]">
                          <TableCell><span className="ml-8 flex items-center gap-2">
                            <FileText size={16} className="shrink-0 text-muted" /><span className="break-all">{doc.title}</span>
                          </span></TableCell>
                          <TableCell><code className="break-all text-xs text-muted">{doc.document_id}</code></TableCell>
                          <TableCell>{doc.chunk_count}</TableCell>
                          <TableCell><StatusBadge status={doc.status} />
                            {doc.status === 2 && doc.error_message && <p className="mt-1 max-w-44 truncate text-xs text-[#a23426]"
                              tabIndex={0} title={doc.error_message}>{doc.error_message}</p>}
                          </TableCell>
                          <TableCell><div className="flex gap-1">
                            {doc.status === 2 && <Button size="sm" variant="outline" disabled={retry.isPending}
                              onClick={() => retry.mutate(doc.document_id)}>{retry.isPending && retry.variables === doc.document_id ? "提交中…" : "重试"}</Button>}
                            {doc.status !== 3 && <Button size="sm" variant="ghost" className="text-[#a23426]"
                              disabled={retry.isPending && retry.variables === doc.document_id}
                              onClick={() => confirmDelete({ kind: "document", id: doc.document_id, name: doc.title, tenantId: base.tenantId })}>删除</Button>}
                          </div></TableCell>
                        </TableRow>
                      )) : <TableRow><TableCell colSpan={5} className="pl-14 text-muted">该知识库暂无文档</TableCell></TableRow>)}
                    </Fragment>)}
                  </TableBody>
                </Table>}
        </section>
      </main>
      {editingId && <EditKnowledgeBaseDialog key={editingId} kbId={editingId} onClose={() => setEditingId(null)}
        onSaved={async (detail) => {
          updateShortcut(detail.tenant_id, detail.kb_id, detail.name);
          await refreshTree();
          setEditingId(null);
        }} />}
      {target && <ConfirmDialog
        title={`删除${target.kind === "knowledge-base" ? "知识库" : "文档"}「${target.name}」？`}
        description={target.kind === "knowledge-base" ? "将删除该库及全部文档，不可恢复。" : "将删除该文档及全部索引片段，不可恢复。"}
        pending={remove.isPending} error={remove.isError ? errorMessage(remove.error) : ""}
        onCancel={() => setTarget(null)} onConfirm={() => remove.mutate(target)} />}
    </div>
  );
}
