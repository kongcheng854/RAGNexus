export interface KnowledgeBase {
  kb_id: string;
  name: string;
  tenant_id: string;
  created_at?: string;
}

export type RetrieveProfile = "speed" | "balanced" | "quality" | "custom";

export type TenantPlan = "free" | "standard" | "pro";

export interface RetrievalIds {
  log_id: string;
  trace_id: string | null;
}

export interface FeedbackRequest {
  trace_id: string;
  log_id?: string;
  score: number;
  comment?: string;
}

export interface FeedbackData {
  feedback_id: string;
  trace_id: string;
  log_id: string;
  score: number;
}

export interface AuthMeData {
  plan: TenantPlan;
  features: {
    allowed_profiles: RetrieveProfile[];
    hybrid_allowed: boolean;
    rerank_allowed: boolean;
    query_rewrite_allowed: boolean;
  };
  limits: { max_kb_per_retrieve: number; retrieve_qps: number; retrieve_daily: number; max_kb: number; max_documents_per_kb: number; max_processing_documents: number };
  usage: { kb_count: number; retrieve_daily_count: number };
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

export type IndexStatus = 1 | 2 | 3;

export interface DocumentTreeItem {
  document_id: string;
  title: string;
  status: IndexStatus;
  error_message: string | null;
  chunk_count: number;
  created_at: string;
}

export interface KnowledgeBaseDetailData extends KnowledgeBase {
  description: string | null;
  settings: Record<string, unknown>;
  document_count: number;
  created_at: string;
  updated_at: string;
}

export interface UpdateKnowledgeBaseRequest {
  name?: string;
  description?: string | null;
  settings?: Record<string, unknown>;
}

export interface DocumentResult {
  document_id: string;
  kb_id: string;
  status: IndexStatus;
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
