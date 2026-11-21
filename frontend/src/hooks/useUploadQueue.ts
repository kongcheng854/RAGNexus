import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { uploadDocument } from "../services/knowledge";
import { makeUploadItems } from "../utils/files";
import { uploadSequentially } from "../utils/upload";
import type { KnowledgeBase, UploadItem } from "../types/api";

export function useUploadQueue() {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [running, setRunning] = useState(false);
  const locked = useRef(false);
  const mutation = useMutation({ mutationFn: uploadDocument, retry: false });

  function addFiles(files: File[]) {
    if (locked.current) return;
    setItems((current) => [...current, ...makeUploadItems(files)]);
  }

  async function start(base: KnowledgeBase) {
    if (locked.current) return;
    const pending = items.filter((item) => item.status === "pending");
    if (!pending.length) return;
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
      );
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
