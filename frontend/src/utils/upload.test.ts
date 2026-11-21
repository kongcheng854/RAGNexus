import { describe, expect, it } from "vitest";
import type { DocumentResult, UploadItem } from "../types/api";
import { readDocument, makeUploadItems } from "./files";
import { uploadSequentially } from "./upload";

function file(name: string, content: string, path = name): File {
  const bytes = new TextEncoder().encode(content);
  return {
    name, size: bytes.length, webkitRelativePath: path,
    arrayBuffer: async () => bytes.buffer,
  } as File;
}

describe("document reading", () => {
  it("preserves markdown and accepts valid UTF-8 text", async () => {
    expect(await readDocument(file("政策.md", "# 退款\n七天内申请"))).toEqual({
      title: "政策.md", content: "# 退款\n七天内申请",
    });
  });
  it("reports unsupported, blank, oversized and malformed files", async () => {
    for (const invalid of [
      file("policy.pdf", "pdf"), file("blank.txt", "  "),
      file("nul.md", "\u0000"),
      file("large.txt", "x".repeat(100_001)),
      { name: "utf8.txt", size: 2, arrayBuffer: async () => new Uint8Array([0xc0, 0xff]).buffer } as File,
    ]) {
      await expect(readDocument(invalid)).rejects.toThrow();
    }
  });
  it("retains the relative folder path", () => {
    const entries = makeUploadItems([file("a.md", "a", "folder/sub/a.md")]);
    expect(entries[0].path).toBe("folder/sub/a.md");
    expect(entries[0].status).toBe("pending");
  });
});

describe("sequential upload", () => {
  it("awaits each request and continues through validation and server errors", async () => {
    const items = makeUploadItems([
      file("first.txt", "first"),
      file("unsupported.pdf", "pdf"),
      file("failed.txt", "failed"),
      file("last.md", "last"),
    ]);
    const updates: Record<string, Partial<UploadItem>> = {};
    const sent: string[] = [];
    let inFlight = 0;
    let maximum = 0;
    const result: DocumentResult = {
      document_id: "doc", kb_id: "kb", status: 1, chunk_count: 2,
    };
    await uploadSequentially(items, async (document) => {
      inFlight++;
      maximum = Math.max(maximum, inFlight);
      sent.push(document.title);
      await new Promise((resolve) => setTimeout(resolve, 2));
      inFlight--;
      if (document.title === "failed.txt") throw new Error("索引失败");
      return result;
    }, (id, patch) => { updates[id] = { ...updates[id], ...patch }; });
    expect(maximum).toBe(1);
    expect(sent).toEqual(["first.txt", "failed.txt", "last.md"]);
    expect(items.map((item) => updates[item.id].status)).toEqual([
      "success", "failed", "failed", "success",
    ]);
    expect(updates[items[1].id].error).toContain("仅支持");
    expect(updates[items[2].id].error).toBe("索引失败");
    expect(updates[items[3].id].result?.chunk_count).toBe(2);
  });
});
