import { errorMessage } from "../lib/axios";
import { readDocument } from "./files";
import type { DocumentResult, UploadItem } from "../types/api";

export async function uploadSequentially(
  items: UploadItem[],
  send: (document: { title: string; content: string }) => Promise<DocumentResult>,
  update: (id: string, patch: Partial<UploadItem>) => void,
): Promise<void> {
  for (const item of items) {
    update(item.id, { status: "uploading", error: undefined });
    try {
      const document = await readDocument(item.file);
      const result = await send(document);
      update(item.id, { status: "success", result });
    } catch (error) {
      update(item.id, { status: "failed", error: errorMessage(error) });
    }
  }
}
