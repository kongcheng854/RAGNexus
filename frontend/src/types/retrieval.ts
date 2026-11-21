export interface TreeDocument {
  document_id: string;
  title: string;
  status: "SUCCESS";
  chunk_count: number;
  created_at: string;
}
export interface TreeKnowledgeBase {
  kb_id: string;
  name: string;
  description: string | null;
  created_at: string;
  documents: TreeDocument[];
}
export interface TenantKnowledgeTree {
  tenant_id: string;
  knowledge_bases: TreeKnowledgeBase[];
}
export type RetrievalMode = "vector" | "bm25" | "hybrid";
export interface RetrievalRequest {
  kb_id: string;
  user_id: "debug_user";
  query: string;
  top_k: number;
  retrieval_options: {
    mode: RetrievalMode;
    vector_top_k?: number;
    bm25_top_k?: number;
    rrf_k?: number;
  };
  rerank_options?: { enabled: true; top_n: number };
}
export interface RetrievedChunk {
  document_id: string;
  chunk_id: string;
  title: string;
  content: string;
  score: number;
  vector_score?: number | null;
  bm25_score?: number | null;
  rerank_score?: number | null;
  vector_rank?: number | null;
  bm25_rank?: number | null;
  retrieval_source?: "vector" | "bm25" | "hybrid" | null;
}
export interface RetrievalResult {
  query: string;
  kb_id: string;
  retrieved_chunks: RetrievedChunk[];
  metadata: {
    top_k: number;
    vector_store: string;
    latency_ms: number;
    retrieval?: {
      mode: RetrievalMode;
      fusion?: string;
      vector_count: number;
      bm25_count: number;
      fused_count: number;
      degraded: boolean;
      degraded_reason?: string;
    };
    rerank?: {
      enabled: boolean;
      provider: string;
      llm_provider?: string;
      model?: string;
      candidate_count: number;
      top_n: number;
      degraded: boolean;
      error?: string;
    };
  };
}
