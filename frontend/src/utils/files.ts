import type { UploadItem } from "../types/api";

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function makeUploadItems(files: File[]): UploadItem[] {
  return files.map((file) => ({
    id: crypto.randomUUID(),
    file,
    path: file.webkitRelativePath || file.name,
    status: "pending",
  }));
}

export async function readDocument(file: File): Promise<{ title: string; content: string }> {
  if (!/\.(txt|md)$/i.test(file.name)) throw new Error("仅支持 .txt 和 .md 文件");
  if (file.size > 400_003) throw new Error("文件过大，正文最多支持 100,000 个字符");
  let content: string;
  try {
    content = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
  } catch {
    throw new Error("文件读取失败，请确认文件可访问且使用 UTF-8 编码");
  }
  if (!content.trim()) throw new Error("文件内容为空");
  if (content.includes("\u0000")) throw new Error("文件包含空字符，无法上传");
  if (Array.from(content).length > 100_000) throw new Error("正文超过 100,000 个字符");
  const title = Array.from(file.name).slice(0, 255).join("");
  return { title, content };
}
