import { isAxiosError } from "axios";
import { api, errorMessage } from "../lib/axios";
import { unwrap } from "./knowledge";
import type { Envelope, FeedbackData, FeedbackRequest } from "../types/api";
import type { RetrievalRequest, RetrievalResult } from "../types/retrieval";

export class RetrievalPlanError extends Error {}

export const ragService = {
  async submitFeedback({ trace_id, log_id, score, comment }: FeedbackRequest, submissionId?: string): Promise<FeedbackData> {
    return unwrap((await api.post<Envelope<FeedbackData>>("/rag/feedback", {
      trace_id, log_id, score, comment,
    }, submissionId ? { headers: { "X-Feedback-Submission-Id": submissionId } } : undefined)).data);
  },

  async retrieve(input: RetrievalRequest): Promise<RetrievalResult> {
    const { kb_id, kb_ids, user_id, query, top_k, retrieval_options, rerank_options, query_options, profile } = input;
    try {
      const { data } = await api.post<Envelope<RetrievalResult>>("/rag/retrieve", {
        kb_id, kb_ids, user_id, query, top_k, retrieval_options, rerank_options, query_options, profile,
      });
      if (data.code === 20013) throw new RetrievalPlanError(data.msg);
      return unwrap(data);
    } catch (error) {
      if (isAxiosError(error) && error.response?.data?.code === 20013) {
        throw new RetrievalPlanError(errorMessage(error));
      }
      throw error;
    }
  },
};
