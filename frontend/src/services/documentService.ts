import { api } from "../lib/http";
import type { DocumentResult, Envelope } from "../types/api";
import { deleteResource, unwrap } from "./knowledge";

export async function uploadDocumentFile(form: FormData): Promise<DocumentResult> {
  return unwrap((await api.post<Envelope<DocumentResult>>("/documents/upload", form)).data);
}

export function deleteDocument(documentId: string) {
  return deleteResource(`/documents/${encodeURIComponent(documentId)}`);
}

export async function reindexDocument(documentId: string): Promise<DocumentResult> {
  return unwrap((await api.post<Envelope<DocumentResult>>(
    `/documents/${encodeURIComponent(documentId)}/reindex`,
  )).data);
}
