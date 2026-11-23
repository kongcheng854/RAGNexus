import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { errorMessage } from "../lib/http";
import { uploadDocumentFile } from "../services/documentService";
import { uploadDocument } from "../services/knowledge";
import { makeUploadItems } from "../utils/files";
import { uploadSequentially } from "../utils/upload";
import type { DocumentResult, KnowledgeBase, UploadItem } from "../types/api";

export function useUploadQueue(onSubmitted: (result: DocumentResult) => void) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [running, setRunning] = useState(false);
  const locked = useRef(false);
  const mutation = useMutation({ mutationFn: uploadDocument, retry: false, onSuccess: onSubmitted });

  const fileMutation = useMutation({ mutationFn: uploadDocumentFile, retry: false, onSuccess: onSubmitted });

  function addFiles(files: File[]) {
    if (locked.current) return;
    setItems((current) => [...current, ...makeUploadItems(files)]);
  }

  async function start(base: KnowledgeBase): Promise<string> {
    if (locked.current) return "";
    const pending = items.filter((item) => item.status === "pending");
    if (!pending.length) return "";
    locked.current = true;
    setRunning(true);
    try {
      await uploadSequentially(
        pending,
        (document) => mutation.mutateAsync({
          kb_id: base.kb_id, ...document,
        }),
        (id, patch) => setItems((current) => current.map((item) =>
          item.id === id ? { ...item, ...patch } : item,
        )),
        (file) => {
          const form = new FormData();
          form.append("kb_id", base.kb_id);
          form.append("file", file);
          return fileMutation.mutateAsync(form);
        },
      );
      return "";
    } catch (error) {
      return errorMessage(error);
    } finally {
      locked.current = false;
      setRunning(false);
    }
  }

  function remove(id: string) {
    if (!locked.current) setItems((current) => current.filter((item) => item.id !== id));
  }

  function clear() {
    if (!locked.current) setItems([]);
  }

  return { items, running, addFiles, start, remove, clear };
}
