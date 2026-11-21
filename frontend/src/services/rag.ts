import { api } from "../lib/axios";
import { unwrap } from "./knowledge";
import type { Envelope } from "../types/api";
import type { RetrievalRequest, RetrievalResult } from "../types/retrieval";

export const ragService = {
  async retrieve(input: RetrievalRequest): Promise<RetrievalResult> {
    const { kb_id, user_id, query, top_k, retrieval_options, rerank_options } = input;
    return unwrap((await api.post<Envelope<RetrievalResult>>("/rag/retrieve", {
      kb_id, user_id, query, top_k, retrieval_options, rerank_options,
    })).data);
  },
};
