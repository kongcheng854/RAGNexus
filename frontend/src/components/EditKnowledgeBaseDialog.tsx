import { useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { errorMessage } from "../lib/http";
import { knowledgeBaseService } from "../services/knowledge";
import type { KnowledgeBaseDetailData, UpdateKnowledgeBaseRequest } from "../types/api";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";

interface Props {
  kbId: string;
  onClose: () => void;
  onSaved: (detail: KnowledgeBaseDetailData) => Promise<void>;
}

export function EditKnowledgeBaseDialog({ kbId, onClose, onSaved }: Props) {
  const detail = useQuery({
    queryKey: ["knowledge-base-detail", kbId],
    queryFn: ({ signal }) => knowledgeBaseService.fetchDetail(kbId, signal),
    gcTime: 0,
  });
  const update = useMutation({
    mutationFn: (body: UpdateKnowledgeBaseRequest) => knowledgeBaseService.updateKnowledgeBase(kbId, body),
    onSuccess: onSaved,
  });
  return (
    <Dialog open onOpenChange={(open) => { if (!open && !update.isPending) onClose(); }}>
      <DialogContent>
        <DialogTitle className="text-lg font-semibold">编辑知识库</DialogTitle>
        <DialogDescription className="mb-5 mt-1 break-all text-xs text-muted">{kbId}</DialogDescription>
        {detail.isPending && <p role="status">正在读取知识库…</p>}
        {detail.isError && <div><p role="alert" className="error-notice">{errorMessage(detail.error)}</p>
          <Button className="mt-3" variant="outline" onClick={() => void detail.refetch()}>重新读取</Button></div>}
        {detail.isSuccess && <EditForm detail={detail.data} pending={update.isPending}
          error={update.isError ? errorMessage(update.error) : ""} onCancel={onClose} onSave={update.mutate} />}
      </DialogContent>
    </Dialog>
  );
}

interface FormProps {
  detail: KnowledgeBaseDetailData;
  pending: boolean;
  error: string;
  onCancel: () => void;
  onSave: (changes: UpdateKnowledgeBaseRequest) => void;
}

function EditForm({ detail, pending, error, onCancel, onSave }: FormProps) {
  const [name, setName] = useState(detail.name);
  const [description, setDescription] = useState(detail.description ?? "");
  const [json, setJson] = useState(() => JSON.stringify(detail.settings, null, 2));
  const [validation, setValidation] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    const changes: UpdateKnowledgeBaseRequest = {};
    const trimmed = name.trim();
    if (!trimmed) {
      setValidation("请填写知识库名称");
      return;
    }
    if (trimmed !== detail.name) changes.name = trimmed;
    if ((description.trim() || null) !== detail.description) changes.description = description.trim() || null;
    let settings: unknown;
    try {
      settings = JSON.parse(json);
    } catch {
      setValidation("settings JSON 格式错误，请检查引号、逗号和括号。");
      return;
    }
    if (!settings || typeof settings !== "object" || Array.isArray(settings)) {
      setValidation("settings 必须是 JSON 对象。清空配置请填写 {}。");
      return;
    }
    if (JSON.stringify(settings) !== JSON.stringify(detail.settings)) {
      changes.settings = settings as Record<string, unknown>;
    }
    setValidation("");
    if (Object.keys(changes).length) onSave(changes);
    else onCancel();
  }

  return <form onSubmit={submit}>
    <fieldset disabled={pending} className="space-y-4">
      <label className="field-label">名称<Input required maxLength={255} value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field-label">描述<textarea className="w-full rounded-lg border p-3" rows={3}
        maxLength={10000} value={description} onChange={(e) => setDescription(e.target.value)} /></label>
      <label className="field-label">settings JSON<textarea className="w-full rounded-lg border p-3 font-mono text-sm"
        spellCheck={false} rows={8} value={json} onChange={(e) => setJson(e.target.value)} /></label>
      <p className="text-xs text-muted">修改 settings 会替换整个配置，保存后对下一次检索生效。</p>
      {(validation || error) && <p role="alert" className="error-notice">{validation || error}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel}>取消</Button>
        <Button type="submit">{pending ? "正在保存…" : "保存"}</Button>
      </div>
    </fieldset>
  </form>;
}
