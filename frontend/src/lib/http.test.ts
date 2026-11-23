import { afterEach, describe, expect, it, vi } from "vitest";
import { AxiosError, AxiosHeaders, type AxiosAdapter } from "axios";
import { api, AUTH_ERROR_MESSAGE, errorMessage } from "./http";
import { createKnowledgeBase, knowledgeBaseService, uploadDocument } from "../services/knowledge";
import { ragService, RetrievalPlanError } from "../services/rag";
import { deleteDocument, reindexDocument } from "../services/documentService";
import { fetchAuthMe } from "../services/authService";

const initialAdapter = api.defaults.adapter;
afterEach(() => { api.defaults.adapter = initialAdapter; vi.unstubAllEnvs(); });

describe("API key transport", () => {
  it("injects the configured key and strips tenant from all write payloads", async () => {
    vi.stubEnv("API_KEY", "frontend-test-key");
    const paths: string[] = [];
    const adapter: AxiosAdapter = async (config) => {
      expect(config.headers.get("Authorization")).toBe("Bearer frontend-test-key");
      if (config.data) expect(JSON.parse(config.data)).not.toHaveProperty("tenant_id");
      expect(config.params || {}).not.toHaveProperty("tenant_id");
      paths.push(config.url!);
      const data = config.url?.endsWith("/upload")
        ? { status: 1, document_id: "doc", kb_id: "kb", chunk_count: 1 }
        : config.url?.endsWith("/tree") ? [] : {};
      return { config, headers: {}, status: 200, statusText: "OK", data: { code: 0, msg: "success", data } };
    };
    api.defaults.adapter = adapter;
    await createKnowledgeBase({ name: "test", ...{ tenant_id: "forged" } });
    await uploadDocument({ kb_id: "kb", title: "test", content: "text", ...{ tenant_id: "forged" } });
    await ragService.retrieve({
      kb_id: "kb", user_id: "debug_user", query: "text", top_k: 5,
      retrieval_options: { mode: "hybrid" }, ...{ tenant_id: "forged" },
    });
    await knowledgeBaseService.fetchTree("knowledge");
    await fetchAuthMe();
    expect(paths).toEqual([
      "/knowledge-bases/create", "/documents/upload", "/rag/retrieve", "/knowledge-bases/tree", "/auth/me",
    ]);
  });

  it("accepts a queued upload and reads both status formats from the tree", async () => {
    const doc = { title: "手册", document_id: "doc", status: 3, chunk_count: 0 };
    api.defaults.adapter = async (config) => {
      const data = config.method === "post" ? doc : [{
        tenant_id: "tenant", knowledge_bases: [{ kb_id: "kb", documents: [
          doc, { ...doc, status: "PROCESSING" }, { ...doc, status: "FAILED", error_message: "模型超时" },
          { ...doc, status: "SUCCESS" },
        ] }],
      }];
      return { config, status: 200, statusText: "OK", headers: {}, data: { code: 0, data } };
    };
    await expect(uploadDocument({ kb_id: "kb", content: "text", title: "手册" })).resolves.toMatchObject({ status: 3 });
    const docs = (await knowledgeBaseService.fetchTree())[0].knowledge_bases[0].documents;
    expect(docs.map(({ status }) => status)).toEqual([3, 3, 2, 1]);
    expect(docs[2].error_message).toBe("模型超时");
  });

  it("handles lifecycle routes, including null data on successful deletion", async () => {
    const calls: string[] = [];
    const changes = { settings: { synonyms: [] } };
    api.defaults.adapter = async (config) => {
      calls.push(`${config.method} ${config.url}`);
      if (config.method === "patch") expect(JSON.parse(config.data)).toEqual(changes);
      const data = config.method === "delete" ? null : { kb_id: "kb", status: 3 };
      return { config, status: 200, statusText: "OK", headers: {}, data: { code: 0, data } };
    };
    await knowledgeBaseService.fetchDetail("kb");
    await knowledgeBaseService.updateKnowledgeBase("kb", changes);
    await reindexDocument("doc");
    await deleteDocument("doc");
    await knowledgeBaseService.deleteKnowledgeBase("kb");
    expect(calls).toEqual(["get /knowledge-bases/kb", "patch /knowledge-bases/kb",
      "post /documents/doc/reindex", "delete /documents/doc", "delete /knowledge-bases/kb"]);
  });

  it.each([200, 429])("preserves the quota reason for create and upload on HTTP %i", async (status) => {
    const msg = "正在索引的文档已达到上限（1 份），请稍后再试";
    api.defaults.adapter = async (config) => {
      const response = { config, headers: new AxiosHeaders(), status, statusText: "", data: { code: 20014, msg, data: null } };
      if (status === 429) throw new AxiosError("Too many requests", "ERR_BAD_REQUEST", config, undefined, response);
      return response;
    };
    for (const action of [
      () => createKnowledgeBase({ name: "资料库" }),
      () => uploadDocument({ kb_id: "kb", title: "手册", content: "内容" }),
    ]) {
      await expect(action().catch((error) => { throw new Error(errorMessage(error)); })).rejects.toThrow(msg);
    }
  });

  it.each([undefined, "d941d05f-24ed-4f53-9176-cd07e42f8041"])("posts feedback with the optional retry token %s", async (submissionId) => {
    const body = { trace_id: "trace-test", log_id: "log-test", score: 4, comment: "相关" };
    api.defaults.adapter = async (config) => {
      expect(config.url).toBe("/rag/feedback");
      expect(config.headers.get("X-Feedback-Submission-Id")).toBe(submissionId);
      expect(JSON.parse(config.data)).toEqual(body);
      return { config, headers: {}, status: 200, statusText: "OK", data: { code: 0, data: { ...body, feedback_id: "score-test" } } };
    };
    await expect(ragService.submitFeedback(body, submissionId)).resolves.toMatchObject({ feedback_id: "score-test", score: 4 });
  });

  it.each([200, 403])("keeps joint scope and exposes the plan error on HTTP %i", async (status) => {
    const kb_ids = ["kb-a", "kb-b"];
    api.defaults.adapter = async (config) => {
      expect(JSON.parse(config.data)).toEqual({ kb_ids, query: "Q", user_id: "debug_user" });
      const response = { config, headers: {}, status, statusText: "", data: { code: 20013, msg: "套餐受限", data: null } };
      if (status === 403) throw new AxiosError("Forbidden", "ERR_BAD_REQUEST", config, undefined, response);
      return response;
    };
    await expect(ragService.retrieve({ kb_ids, query: "Q", user_id: "debug_user" })).rejects.toBeInstanceOf(RetrievalPlanError);
  });

  it("sends a quality request without custom options", async () => {
    const payload = { kb_id: "kb", user_id: "debug_user", query: "检索", profile: "quality" } as const;
    api.defaults.adapter = async (config) => {
      expect(JSON.parse(config.data)).toEqual(payload);
      return { config, headers: {}, status: 200, statusText: "OK", data: { code: 0, data: {} } };
    };
    await ragService.retrieve(payload);
  });

  it("sends no Authorization header when the key is blank", async () => {
    vi.stubEnv("API_KEY", " ");
    api.defaults.adapter = async (config) => {
      expect(config.headers.has("Authorization")).toBe(false);
      return { config, headers: {}, status: 200, statusText: "OK", data: { code: 0, data: {} } };
    };
    await fetchAuthMe();
  });

  it.each([200, 401])("normalizes authorization failures delivered as HTTP %i", async (status) => {
    api.defaults.adapter = async (config) => {
      const response = {
        config, headers: new AxiosHeaders(), status, statusText: "Unauthorized",
        data: { code: 20010, msg: "unauthorized", data: null },
      };
      if (status === 401) throw new AxiosError("Unauthorized", "ERR_BAD_REQUEST", config, undefined, response);
      return response;
    };
    await expect(fetchAuthMe()).rejects.toThrow(AUTH_ERROR_MESSAGE);
  });
});
