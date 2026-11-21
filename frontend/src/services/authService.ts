import { api } from "../lib/http";
import { unwrap } from "./knowledge";
import type { AuthMeData, Envelope } from "../types/api";

export async function fetchAuthMe(signal?: AbortSignal): Promise<AuthMeData> {
  return unwrap((await api.get<Envelope<AuthMeData>>("/auth/me", { signal })).data);
}
