import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AppHeader } from "../components/AppHeader";
import { TenantBadges } from "../components/TenantBadges";
import { useAuthMe } from "../hooks/useAuthMe";
import { readScope } from "../utils/navigation";
import { BookOpen, Check, ChevronDown, CircleAlert, FileStack, FileText, FolderOpen, Layers3, LoaderCircle, Plus, Upload, X } from "lucide-react";
import { Button } from "../components/ui/button";
import { KnowledgeBaseDialog } from "../components/KnowledgeBaseDialog";
import { ExistingBaseDialog } from "../components/ExistingBaseDialog";
import { UploadTable } from "../components/UploadTable";
import { useKnowledgeBases } from "../hooks/useKnowledgeBases";
import { useUploadQueue } from "../hooks/useUploadQueue";
import type { KnowledgeBase } from "../types/api";

export function KnowledgePage() {
  const [params] = useSearchParams();
  const scope = readScope(params);
  const auth = useAuthMe();
  const tenant = auth.isSuccess ? auth.data.tenant_id : "";
  const { bases, remember, storageWarning } = useKnowledgeBases();
  const [selected, setSelected] = useState<KnowledgeBase | null>(null);
  const [creating, setCreating] = useState(false);
  const [existing, setExisting] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [notice, setNotice] = useState("");
  const queue = useUploadQueue();
  const files = useRef<HTMLInputElement>(null);
  const folder = useRef<HTMLInputElement>(null);
  const visibleBases = bases.filter((base) => base.tenant_id === tenant);
  if (tenant && selected?.tenant_id === tenant && !visibleBases.some((base) => base.kb_id === selected.kb_id)) {
    visibleBases.push(selected);
  }
  const active = tenant && selected?.tenant_id === tenant ? selected : null;
  const successful = queue.items.filter((item) => item.status === "success").length;
  const failed = queue.items.filter((item) => item.status === "failed").length;
  const pending = queue.items.filter((item) => item.status === "pending").length;
  const completed = successful + failed;
  const hasResults = completed > 0;
  const progress = queue.items.length ? completed / queue.items.length * 100 : 0;

  useEffect(() => {
    if (scope.valid && tenant) {
      setSelected({ tenant_id: tenant, kb_id: scope.kbId, name: "已选知识库" });
    }
  }, [tenant, scope.kbId, scope.valid]);

  useEffect(() => {
    if (!queue.running) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [queue.running]);

  function selectBase(base: KnowledgeBase) {
    if (queue.running) return;
    if (active?.kb_id !== base.kb_id && queue.items.length) queue.clear();
    setSelected(base);
    setNotice("");
  }

  function acceptFiles(chosen: File[]) {
    if (queue.running || !active) return;
    setNotice("");
    queue.addFiles(chosen);
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a href="/" className="brand" aria-label="RAGNexus 首页"><span className="brand-icon"><Layers3 size={24} /></span><span>RAG<span className="font-normal">Nexus</span><small>知识库管理</small></span></a>
        <div className="workspace-label">工作空间</div>
        <div className="sidebar-active"><BookOpen size={19} /><span>知识库</span><span className="ml-auto text-xs">{visibleBases.length}</span></div>
        <div className="mt-9 flex items-center justify-between px-3"><span className="text-sm text-muted">本地知识库记录</span><Button size="icon" variant="ghost" disabled={queue.running} aria-label="新建知识库" onClick={() => setCreating(true)}><Plus size={17} /></Button></div>
        <div className="mt-1 space-y-1">
          {visibleBases.map((base) => (
            <button key={base.kb_id} disabled={queue.running} onClick={() => selectBase(base)} className={`base-link ${active?.kb_id === base.kb_id ? "base-link-selected" : ""}`}>
              <span className="base-dot" /><span className="truncate">{base.name}</span>{active?.kb_id === base.kb_id && <Check size={15} className="ml-auto shrink-0" />}
            </button>
          ))}
          {!visibleBases.length && <p className="px-3 py-3 text-sm leading-6 text-muted">创建知识库后，<br />会显示在这里。</p>}
        </div>
        <div className="sidebar-footer"><span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-white"><FileStack size={17} /></span><span>文档工作台<small>导入 · 切片 · 索引</small></span></div>
      </aside>

      <div className="main-shell">
        <AppHeader locked={queue.running} />
        <main className="content">
          <div className="page-heading"><div><p className="eyebrow">KNOWLEDGE WORKSPACE</p><h1>让知识，准备就绪。</h1><p className="mt-3 text-base text-muted">创建知识库，导入文档，为检索调优准备好素材。</p></div><Button disabled={queue.running} onClick={() => setCreating(true)}><Plus size={17} />创建知识库</Button></div>

          <section className="context-card" aria-label="上传目标">
            <div className="context-icon"><BookOpen size={23} /></div>
            <div className="min-w-0 flex-1"><label htmlFor="knowledge-base" className="mb-2 block text-sm text-muted">当前知识库</label>
              <div className="relative"><select id="knowledge-base" aria-label="当前知识库" disabled={queue.running} value={active?.kb_id || ""} onChange={(event) => { const base = visibleBases.find((entry) => entry.kb_id === event.target.value); if (base) selectBase(base); }} className="w-full appearance-none bg-transparent pr-7 text-lg font-semibold outline-none focus-visible:ring-2 focus-visible:ring-primary">
                <option value="" disabled>选择或创建知识库</option>{visibleBases.map((base) => <option key={base.kb_id} value={base.kb_id}>{base.name}</option>)}
              </select><ChevronDown size={17} className="pointer-events-none absolute right-0 top-1.5 text-muted" /></div>
              {active && <p className="mt-2 break-all text-xs text-muted">ID {active.kb_id}</p>}
            </div>
            <div className="context-divider" />
            <TenantBadges />
            <Button variant="ghost" size="sm" disabled={queue.running || !tenant} onClick={() => setExisting(true)}>使用已有 ID</Button>
          </section>
          {storageWarning && <p className="error-notice mt-3" role="alert">{storageWarning}</p>}

          <section className="upload-card">
            <div className="flex flex-wrap items-start justify-between gap-4 p-6"><div><h2 className="text-lg font-semibold">导入知识文档</h2><p className="mt-1.5 text-sm text-muted">选择文件或整个文件夹，系统将逐个上传并同步建立索引。</p></div><span className="flex items-center gap-2 text-sm text-muted"><FileText size={16} />TXT / Markdown</span></div>
            <input ref={files} className="sr-only" tabIndex={-1} aria-label="选择知识文件" type="file" multiple accept=".txt,.md" onChange={(event) => { acceptFiles(Array.from(event.target.files || [])); event.target.value = ""; }} />
            <input ref={folder} className="sr-only" tabIndex={-1} aria-label="选择知识文件夹" type="file" multiple {...{ webkitdirectory: "", directory: "" }} onChange={(event) => { acceptFiles(Array.from(event.target.files || [])); event.target.value = ""; }} />
            <div className={`drop-zone ${dragging ? "drop-zone-active" : ""}`} onDragOver={(event) => { event.preventDefault(); if (!queue.running && active) setDragging(true); }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }} onDrop={(event) => {
              event.preventDefault(); setDragging(false);
              const entries = Array.from(event.dataTransfer.items);
              if (entries.some((item) => item.webkitGetAsEntry?.()?.isDirectory)) {
                setNotice("拖入了文件夹，请使用「选择文件夹」按钮导入。"); return;
              }
              acceptFiles(Array.from(event.dataTransfer.files));
            }}>
              <div className="upload-icon"><Upload size={28} strokeWidth={1.5} /></div>
              <h3 className="mt-5 text-lg font-semibold">{active ? "把文档拖到这里，或选择上传" : "先选择一个知识库"}</h3>
              <p className="mb-6 mt-2 text-sm text-muted">UTF-8 编码的 .txt、.md 文件，单个文件最多 100,000 字符</p>
              <div className="flex flex-wrap justify-center gap-3"><Button variant="outline" disabled={!active || queue.running} onClick={() => files.current?.click()}><FileText size={16} />选择文件</Button><Button variant="outline" disabled={!active || queue.running} onClick={() => folder.current?.click()}><FolderOpen size={17} />选择文件夹</Button></div>
            </div>
            {notice && <p role="alert" className="error-notice mx-6 mb-5">{notice}</p>}
            <div className="queue-heading"><h3 className="text-base font-semibold">上传队列 <span className="ml-2 text-sm font-normal text-muted">{queue.items.length} 个文件</span></h3><Button variant="ghost" size="sm" disabled={queue.running || !queue.items.length} onClick={queue.clear}><X size={15} />清空队列</Button></div>
            {queue.items.length ? <UploadTable items={queue.items} running={queue.running} remove={queue.remove} /> : <div className="empty-queue"><FileStack size={30} strokeWidth={1.2} /><p className="mt-3 text-sm">队列还是空的，添加第一份知识文档吧。</p></div>}
            {(queue.running || hasResults) && <div className="progress-section" aria-live="polite"><div className="mb-3 flex flex-wrap justify-between gap-2 text-sm"><span>{queue.running ? `正在处理 ${completed + 1} / ${queue.items.length}，请保持页面打开` : `已处理 ${completed} / ${queue.items.length} 个文件`}</span><span><span className="text-primary">成功 {successful}</span><span className="mx-3 text-[#c2cec7]">|</span><span className={failed ? "text-[#b34636]" : "text-muted"}>失败 {failed}</span></span></div><div role="progressbar" aria-label="文件处理进度" aria-valuenow={completed} aria-valuemin={0} aria-valuemax={queue.items.length} className="h-1.5 overflow-hidden rounded-full bg-[#e9eeeb]"><div className="h-full rounded-full bg-primary transition-all duration-300" style={{ width: `${progress}%` }} /></div></div>}
            <div className="upload-footer"><p className="flex items-center gap-2 text-sm text-muted"><CircleAlert size={15} className="shrink-0" />{hasResults && failed ? "失败原因显示在对应文件下，修正后可重新选择文件上传。" : "文件按队列顺序处理，完成后可查看每份文档的结果。"}</p><Button disabled={!active || queue.running || !pending} onClick={() => { if (active) void queue.start(active); }}>{queue.running ? <LoaderCircle size={16} className="animate-spin" /> : <Upload size={16} />}{queue.running ? "正在上传" : `开始上传${pending ? ` · ${pending} 个文件` : ""}`}</Button></div>
          </section>
          <p className="footer-note">知识库记录保存在当前浏览器，文档提交至后端服务。</p>
        </main>
      </div>
      {creating && <KnowledgeBaseDialog open onClose={() => setCreating(false)} onCreated={(base) => { remember(base); selectBase(base); }} />}
      {existing && <ExistingBaseDialog tenant={tenant} onClose={() => setExisting(false)} onSelected={(base) => { remember(base); selectBase(base); }} />}
    </div>
  );
}
