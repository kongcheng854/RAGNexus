import { z } from "zod";

export function scopeUrl(path: "/" | "/retrieve", kbId: string): string {
  return path + "?" + new URLSearchParams({ kb_id: kbId }).toString();
}

export function readScope(params: URLSearchParams) {
  const kbId = params.get("kb_id") || "";
  return { kbId, valid: z.string().uuid().safeParse(kbId).success };
}
