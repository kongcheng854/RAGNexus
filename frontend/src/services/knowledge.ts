import { api } from "../lib/axios";
import type { CreateKnowledgeBaseRequest, DocumentResult, Envelope, KnowledgeBase, UploadDocumentRequest } from "../types/api";
import type { TenantKnowledgeTree } from "../types/retrieval";

export function unwrap<T>(envelope: Envelope<T>): T {
  if (envelope.code !== 0 || envelope.data === null) {
    throw new Error(envelope.msg || "操作失败");
  }
  return envelope.data;
}

export const knowledgeBaseService = {
  async fetchTree(keyword?: string, signal?: AbortSignal): Promise<TenantKnowledgeTree[]> {
    return unwrap((await api.get<Envelope<TenantKnowledgeTree[]>>("/knowledge-bases/tree", {
      params: keyword ? { keyword } : undefined, signal,
    })).data);
  },
};

export async function createKnowledgeBase(input: CreateKnowledgeBaseRequest): Promise<KnowledgeBase> {
  const { name, description } = input;
  return unwrap((await api.post<Envelope<KnowledgeBase>>("/knowledge-bases/create", { name, description })).data);
}

export async function uploadDocument(input: UploadDocumentRequest): Promise<DocumentResult> {
  const { kb_id, title, content } = input;
  const result = unwrap((await api.post<Envelope<DocumentResult>>("/documents/upload", { kb_id, title, content })).data);
  if (result.status !== 1) throw new Error("文档索引未成功完成");
  return result;
}
