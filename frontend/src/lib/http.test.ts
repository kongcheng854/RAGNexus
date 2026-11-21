import { afterEach, describe, expect, it, vi } from "vitest";
import { AxiosError, AxiosHeaders, type AxiosAdapter } from "axios";
import { api, AUTH_ERROR_MESSAGE } from "./http";
import { createKnowledgeBase, knowledgeBaseService, uploadDocument } from "../services/knowledge";
import { ragService } from "../services/rag";
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
