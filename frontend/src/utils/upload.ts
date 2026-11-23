import { AuthConfigurationError, errorMessage } from "../lib/http";
import { isSupportedUploadFile, isBinaryUploadFile, readDocument } from "./files";
import type { DocumentResult, UploadItem } from "../types/api";

export async function uploadSequentially(
  items: UploadItem[],
  send: (document: { title: string; content: string }) => Promise<DocumentResult>,
  update: (id: string, patch: Partial<UploadItem>) => void,
  sendFile: (file: File) => Promise<DocumentResult>,
): Promise<void> {
  for (const item of items) {
    update(item.id, { status: "uploading", error: undefined });
    try {
      if (!isSupportedUploadFile(item.file)) throw new Error("仅支持 .txt、.md、.pdf 和 .docx 文件");
      const result = isBinaryUploadFile(item.file)
        ? await sendFile(item.file)
        : await send(await readDocument(item.file));
      update(item.id, { status: "success", result });
    } catch (error) {
      if (error instanceof AuthConfigurationError) {
        update(item.id, { status: "pending" });
        throw error;
      }
      update(item.id, { status: "failed", error: errorMessage(error) });
    }
  }
}
