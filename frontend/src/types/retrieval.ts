import type { DocumentTreeItem, RetrievalIds, RetrieveProfile, TenantPlan } from "./api";

export interface TreeKnowledgeBase {
  kb_id: string;
  name: string;
  description: string | null;
  created_at: string;
  documents: DocumentTreeItem[];
}
export interface TenantKnowledgeTree {
  tenant_id: string;
  knowledge_bases: TreeKnowledgeBase[];
}
export type RetrievalMode = "vector" | "bm25" | "hybrid";
export type RetrievalRequest = ({ kb_id: string; kb_ids?: never } | { kb_ids: string[]; kb_id?: never }) & {
  profile?: RetrieveProfile;
  user_id: "debug_user";
  query: string;
  top_k?: number;
  retrieval_options?: {
    mode: RetrievalMode;
    vector_top_k?: number;
    bm25_top_k?: number;
    rrf_k?: number;
  };
  query_options?: { enabled: boolean; strategy: "rewrite" };
  rerank_options?: { enabled: true; top_n: number };
};
export interface RetrievedChunk {
  kb_id: string;
  kb_name?: string | null;
  document_id: string;
  chunk_id: string;
  title: string;
  content: string;
  score: number;
  metadata?: { heading_path?: string | null; chunk_type?: string | null; table_part?: number | null } | null;
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
  kb_ids: string[];
  retrieved_chunks: RetrievedChunk[];
  metadata: RetrievalIds & {
    top_k: number;
    vector_store: string;
    latency_ms: number;
    tenant_policy: { plan: TenantPlan; retrieve_profile: RetrieveProfile; effective_mode: RetrievalMode; effective_rerank: boolean; effective_query_rewrite: boolean };
    query_processing?: {
      raw_query: string;
      effective_query: string;
      search_query: string;
      strategy: "noop" | "rewrite";
      rewrite_latency_ms: number;
      degraded: boolean;
      degraded_reason?: string | null;
      synonym_applied: boolean;
      synonym_expansions: string[];
    } | null;
    retrieval?: {
      mode: RetrievalMode;
      fusion?: string;
      multi_kb?: boolean;
      kb_count?: number;
      per_kb_top_k?: number;
      rrf_k?: number;
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
