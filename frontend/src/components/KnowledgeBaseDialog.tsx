import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { z } from "zod";
import { LoaderCircle } from "lucide-react";
import { createKnowledgeBase } from "../services/knowledge";
import { errorMessage } from "../lib/axios";
import type { KnowledgeBase } from "../types/api";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";

const schema = z.object({
  name: z.string().trim().min(1, "请填写知识库名称").max(255, "名称最多 255 个字符"),
  description: z.string().trim().max(10_000),
});
type Values = z.infer<typeof schema>;

export function KnowledgeBaseDialog({ open, onClose, onCreated }: {
  open: boolean; onClose: () => void; onCreated: (base: KnowledgeBase) => void;
}) {
  const [failure, setFailure] = useState("");
  const form = useForm<Values>({
    resolver: zodResolver(schema), defaultValues: { name: "", description: "" },
  });
  const mutation = useMutation({ mutationFn: createKnowledgeBase });
  async function submit(values: Values) {
    setFailure("");
    try {
      const base = await mutation.mutateAsync({
        ...values, description: values.description || undefined,
      });
      onCreated(base);
      onClose();
    } catch (error) { setFailure(errorMessage(error)); }
  }

  return (
    <Dialog open={open} onOpenChange={(value) => { if (!value && !mutation.isPending) onClose(); }}>
      <DialogContent onEscapeKeyDown={(event) => { if (mutation.isPending) event.preventDefault(); }}>
        <DialogTitle className="text-xl font-semibold">创建知识库</DialogTitle>
        <DialogDescription className="mb-6 mt-2 text-sm text-muted">为一组相关文档创建独立的知识空间。</DialogDescription>
        <form onSubmit={form.handleSubmit(submit)} className="space-y-5">
          <fieldset disabled={mutation.isPending} className="space-y-5">
            <label className="field-label">知识库名称<Input {...form.register("name")} placeholder="例如：客户服务知识库" autoFocus />
              <span className="field-error">{form.formState.errors.name?.message}</span>
            </label>
            <label className="field-label">描述 <span className="font-normal text-muted">（选填）</span>
              <textarea {...form.register("description")} rows={3} className="w-full resize-y rounded-lg border border-border p-3 text-base outline-none focus:border-primary" placeholder="这个知识库将收录什么内容？" />
              <span className="field-error">{form.formState.errors.description?.message}</span>
            </label>
          </fieldset>
          {failure && <p role="alert" className="error-notice">{failure}</p>}
          <div className="flex justify-end gap-3 pt-1">
            <Button type="button" variant="outline" onClick={onClose} disabled={mutation.isPending}>取消</Button>
            <Button type="submit" disabled={mutation.isPending}>{mutation.isPending && <LoaderCircle className="animate-spin" size={16} />}创建知识库</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
