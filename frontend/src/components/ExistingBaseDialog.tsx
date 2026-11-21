import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import type { KnowledgeBase } from "../types/api";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";
import { Button } from "./ui/button";

const schema = z.object({
  kb_id: z.string().trim().uuid("请填写有效的知识库 UUID"),
  name: z.string().trim().min(1, "请填写显示名称").max(255),
});

export function ExistingBaseDialog({ tenant, onClose, onSelected }: {
  tenant: string; onClose: () => void; onSelected: (base: KnowledgeBase) => void;
}) {
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema), defaultValues: { kb_id: "", name: "" },
  });
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent>
        <DialogTitle className="text-xl font-semibold">使用已有知识库</DialogTitle>
        <DialogDescription className="mb-6 mt-2 text-sm text-muted">填写已有知识库的信息，上传时会由后端校验。</DialogDescription>
        <form className="space-y-5" onSubmit={form.handleSubmit((values) => { onSelected({ ...values, tenant_id: tenant }); onClose(); })}>
          <label className="field-label">知识库 ID<Input {...form.register("kb_id")} placeholder="UUID" />
            <span className="field-error">{form.formState.errors.kb_id?.message}</span>
          </label>
          <label className="field-label">显示名称<Input {...form.register("name")} />
            <span className="field-error">{form.formState.errors.name?.message}</span>
          </label>
          <div className="flex justify-end"><Button type="submit">使用此知识库</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
