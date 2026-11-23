import { useRef } from "react";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";

interface Props {
  title: string;
  description: string;
  pending: boolean;
  error: string;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmDialog({ title, description, pending, error, onCancel, onConfirm }: Props) {
  const cancel = useRef<HTMLButtonElement>(null);
  return (
    <Dialog open onOpenChange={(open) => { if (!open && !pending) onCancel(); }}>
      <DialogContent onOpenAutoFocus={(event) => { event.preventDefault(); cancel.current?.focus(); }}>
        <DialogTitle className="break-all pr-7 text-lg font-semibold">{title}</DialogTitle>
        <DialogDescription className="my-4 text-sm text-muted">{description}</DialogDescription>
        {error && <p className="error-notice" role="alert">{error}</p>}
        <div className="mt-6 flex justify-end gap-2">
          <Button ref={cancel} variant="outline" disabled={pending} onClick={onCancel}>取消</Button>
          <Button className="bg-[#a23426] hover:bg-[#882b20]" disabled={pending} onClick={onConfirm}>
            {pending ? "正在删除…" : "确认删除"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
