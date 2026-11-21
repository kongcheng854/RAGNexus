export interface KnowledgeBase {
  kb_id: string;
  name: string;
  tenant_id: string;
  created_at?: string;
}

export interface AuthMeData {
  tenant_id: string;
  tenant_name: string;
  key_prefix: string | null;
  key_name: string | null;
}

export interface CreateKnowledgeBaseRequest {
  name: string;
  description?: string;
}

export interface UploadDocumentRequest {
  kb_id: string;
  title: string;
  content: string;
}

export interface DocumentResult {
  document_id: string;
  kb_id: string;
  status: number;
  chunk_count: number;
}

export interface Envelope<T> {
  code: number;
  msg: string;
  data: T | null;
}

export type UploadStatus = "pending" | "uploading" | "success" | "failed";

export interface UploadItem {
  id: string;
  file: File;
  path: string;
  status: UploadStatus;
  error?: string;
  result?: DocumentResult;
}
