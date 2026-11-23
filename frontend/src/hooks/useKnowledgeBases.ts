import { useState } from "react";
import { z } from "zod";
import type { KnowledgeBase } from "../types/api";

const storageKey = "ragnexus.knowledge-bases.v1";
const storedBase = z.object({
  kb_id: z.string().uuid(),
  name: z.string(),
  tenant_id: z.string(),
  created_at: z.string().optional(),
});

function loadBases(): KnowledgeBase[] {
  try {
    const value = storedBase.array().safeParse(JSON.parse(localStorage.getItem(storageKey) || "[]"));
    return value.success ? value.data : [];
  } catch {
    return [];
  }
}

export function useKnowledgeBases() {
  const [bases, setBases] = useState(loadBases);
  const [storageWarning, setStorageWarning] = useState("");

  function save(next: KnowledgeBase[]) {
    setBases(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
      setStorageWarning("");
    } catch {
      setStorageWarning("浏览器无法保存知识库记录，请保留知识库 ID，方便下次使用。");
    }
  }

  function remember(base: KnowledgeBase) {
    save([...bases.filter((entry) =>
      entry.kb_id !== base.kb_id || entry.tenant_id !== base.tenant_id,
    ), base]);
  }

  function updateShortcut(tenantId: string, kbId: string, newName?: string) {
    const current = loadBases();
    const index = current.findIndex((base) => base.tenant_id === tenantId && base.kb_id === kbId);
    if (index === -1) return;
    if (newName === undefined) current.splice(index, 1);
    else current[index] = { ...current[index], name: newName };
    save(current);
  }

  return { bases, remember, updateShortcut, storageWarning };
}
