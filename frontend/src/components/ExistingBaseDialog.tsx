import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";
import { Button } from "./ui/button";

const schema = z.object({
  kb_id: z.string().trim().uuid("请填写有效的知识库 UUID"),
});

export function ExistingBaseDialog({ onClose, onSelected }: {
  onClose: () => void; onSelected: (kbId: string) => void;
}) {
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema), defaultValues: { kb_id: "" },
  });
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent>
        <DialogTitle className="text-xl font-semibold">使用已有知识库</DialogTitle>
        <DialogDescription className="mb-6 mt-2 text-sm text-muted">填写已有知识库的 ID，上传时会由后端校验。</DialogDescription>
        <form className="space-y-5" onSubmit={form.handleSubmit((values) => { onSelected(values.kb_id); onClose(); })}>
          <label className="field-label">知识库 ID<Input {...form.register("kb_id")} placeholder="UUID" />
            <span className="field-error">{form.formState.errors.kb_id?.message}</span>
          </label>
          <div className="flex justify-end"><Button type="submit">使用此知识库</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
