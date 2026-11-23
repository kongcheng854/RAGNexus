import { api } from "../lib/axios";
import type { CreateKnowledgeBaseRequest, DocumentResult, DocumentTreeItem, Envelope, IndexStatus, KnowledgeBase, KnowledgeBaseDetailData, UpdateKnowledgeBaseRequest, UploadDocumentRequest } from "../types/api";
import type { TenantKnowledgeTree, TreeKnowledgeBase } from "../types/retrieval";

export function unwrap<T>(envelope: Envelope<T>): T {
  if (envelope.code !== 0 || envelope.data === null) {
    throw new Error(envelope.msg || "操作失败");
  }
  return envelope.data;
}

const statusNumbers = { SUCCESS: 1, FAILED: 2, PROCESSING: 3 } as const;
type WireDocument = Omit<DocumentTreeItem, "status" | "error_message"> & {
  status: keyof typeof statusNumbers | IndexStatus;
  error_message?: string | null;
};
type WireTree = Omit<TenantKnowledgeTree, "knowledge_bases"> & {
  knowledge_bases: (Omit<TreeKnowledgeBase, "documents"> & { documents: WireDocument[] })[];
};

export async function deleteResource(path: string): Promise<void> {
  const { data } = await api.delete<Envelope<null>>(path);
  if (data.code !== 0) throw new Error(data.msg || "删除失败");
}

export const knowledgeBaseService = {
  async fetchTree(keyword?: string, signal?: AbortSignal): Promise<TenantKnowledgeTree[]> {
    const tenants = unwrap((await api.get<Envelope<WireTree[]>>("/knowledge-bases/tree", {
      params: keyword ? { keyword } : undefined, signal,
    })).data);
    return tenants.map((tenant) => ({
      ...tenant,
      knowledge_bases: tenant.knowledge_bases.map((base) => ({
        ...base,
        documents: base.documents.map((doc) => ({
          ...doc,
          status: typeof doc.status === "number" ? doc.status : statusNumbers[doc.status],
          error_message: doc.error_message ?? null,
        })),
      })),
    }));
  },

  async fetchDetail(kbId: string, signal?: AbortSignal): Promise<KnowledgeBaseDetailData> {
    return unwrap((await api.get<Envelope<KnowledgeBaseDetailData>>(
      `/knowledge-bases/${encodeURIComponent(kbId)}`, { signal },
    )).data);
  },

  async updateKnowledgeBase(kbId: string, body: UpdateKnowledgeBaseRequest): Promise<KnowledgeBaseDetailData> {
    return unwrap((await api.patch<Envelope<KnowledgeBaseDetailData>>(
      `/knowledge-bases/${encodeURIComponent(kbId)}`, body,
    )).data);
  },

  deleteKnowledgeBase(kbId: string) {
    return deleteResource(`/knowledge-bases/${encodeURIComponent(kbId)}`);
  },
};

export async function createKnowledgeBase(input: CreateKnowledgeBaseRequest): Promise<KnowledgeBase> {
  const { name, description } = input;
  return unwrap((await api.post<Envelope<KnowledgeBase>>("/knowledge-bases/create", { name, description })).data);
}

export async function uploadDocument(input: UploadDocumentRequest): Promise<DocumentResult> {
  const { kb_id, title, content } = input;
  const result = unwrap((await api.post<Envelope<DocumentResult>>("/documents/upload", { kb_id, title, content })).data);
  if (result.status !== 3 && result.status !== 1) throw new Error("文档索引失败，请在知识库列表查看原因");
  return result;
}
