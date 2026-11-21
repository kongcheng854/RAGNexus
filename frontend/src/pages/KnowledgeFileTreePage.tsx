import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, ChevronDown, ChevronRight, Copy, FileText, LoaderCircle, RefreshCw, Search } from "lucide-react";
import { AppHeader } from "../components/AppHeader";
import { TenantBadges } from "../components/TenantBadges";
import { Button, buttonVariants } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { knowledgeBaseService } from "../services/knowledge";
import { errorMessage } from "../lib/http";
import { scopeUrl } from "../utils/navigation";

export function KnowledgeFileTreePage() {
  const [keyword, setKeyword] = useState("");
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState(new Set<string>());
  const [copyNotice, setCopyNotice] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setFilter(keyword.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [keyword]);
  const query = useQuery({
    queryKey: ["knowledge-tree", filter],
    queryFn: ({ signal }) => knowledgeBaseService.fetchTree(filter, signal),
  });
  const bases = query.data?.flatMap((tenant) => tenant.knowledge_bases) || [];

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
          <div><p className="eyebrow">KNOWLEDGE LIBRARY</p><h1>知识库列表</h1><p className="mt-3 text-muted">查看当前租户的知识库和已成功索引的文档。</p></div>
          <div className="flex flex-wrap items-center gap-4"><TenantBadges /><Button variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw size={16} className={query.isFetching ? "animate-spin" : ""} />刷新</Button></div>
        </div>
        <section className="panel">
          <div className="flex flex-wrap items-center justify-between gap-4 p-6">
            <label className="relative block w-full max-w-md"><Search className="pointer-events-none absolute left-3 top-3 text-muted" size={18} /><Input aria-label="搜索知识库名称" maxLength={128} placeholder="搜索知识库名称" value={keyword} onChange={(event) => setKeyword(event.target.value)} className="pl-10" /></label>
            <span className="text-sm text-muted">{query.isFetching ? "正在读取…" : `${bases.length} 个知识库`}</span>
          </div>
          {copyNotice && <p role="status" className="mx-6 mb-4 break-all text-sm text-primary">{copyNotice}</p>}
          {query.isError ? <div className="p-6 pt-0"><p role="alert" className="error-notice">{errorMessage(query.error)}</p></div>
            : query.isPending ? <div className="page-empty"><LoaderCircle size={24} className="animate-spin" /><p>正在读取知识库…</p></div>
              : !bases.length ? <div className="page-empty"><BookOpen size={30} strokeWidth={1.4} /><p>{filter ? "没有匹配的知识库" : "还没有知识库"}</p><Link to="/" className={buttonVariants({ variant: "outline", size: "sm" })}>去创建知识库</Link></div>
                : <Table className="min-w-[760px]">
                  <TableHeader><TableRow><TableHead className="w-[38%]">知识库 / 文档</TableHead><TableHead>kb_id/document_id</TableHead><TableHead className="whitespace-nowrap">片段数</TableHead><TableHead>操作</TableHead></TableRow></TableHeader>
                  <TableBody>
                    {bases.map((base) => <Fragment key={base.kb_id}>
                      <TableRow>
                        <TableCell><button className="tree-toggle" aria-expanded={expanded.has(base.kb_id)} onClick={() => toggle(base.kb_id)}>{expanded.has(base.kb_id) ? <ChevronDown size={16} /> : <ChevronRight size={16} />}<BookOpen size={17} className="shrink-0 text-primary" /><span className="break-words font-medium">{base.name}</span></button>{base.description && <p className="ml-10 mt-2 max-w-sm break-words text-xs text-muted">{base.description}</p>}</TableCell>
                        <TableCell><code className="break-all text-xs">{base.kb_id}</code></TableCell>
                        <TableCell className="text-muted">{base.documents.reduce((sum, doc) => sum + doc.chunk_count, 0)}</TableCell>
                        <TableCell><div className="flex items-center gap-1"><Button variant="ghost" size="icon" onClick={() => void copyId(base.kb_id)} aria-label={`复制 ${base.name} 的知识库 ID`}><Copy size={15} /></Button><Link className={buttonVariants({ variant: "ghost", size: "sm" })} to={scopeUrl("/", base.kb_id)}>去上传</Link><Link className={buttonVariants({ variant: "outline", size: "sm" })} to={scopeUrl("/retrieve", base.kb_id)}>去检索</Link></div></TableCell>
                      </TableRow>
                      {expanded.has(base.kb_id) && (base.documents.length ? base.documents.map((doc) => <TableRow key={doc.document_id} className="bg-[#fcfdfc]"><TableCell><span className="ml-8 flex items-center gap-2"><FileText size={16} className="shrink-0 text-muted" /><span className="break-all">{doc.title}</span></span></TableCell><TableCell><code className="break-all text-xs text-muted">{doc.document_id}</code></TableCell><TableCell>{doc.chunk_count}</TableCell><TableCell><span className="rounded-md bg-[#e9f4eb] px-2 py-1 text-xs text-[#287448]">已索引</span></TableCell></TableRow>) : <TableRow><TableCell colSpan={4} className="pl-14 text-muted">该知识库暂无成功索引的文档</TableCell></TableRow>)}
                    </Fragment>)}
                  </TableBody>
                </Table>}
        </section>
      </main>
    </div>
  );
}
