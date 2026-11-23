import axios from "axios";

export const AUTH_ERROR_MESSAGE = "API Key 无效或未配置，请检查 frontend/.env 中的 API_KEY";

function quotaMessage(message: unknown): string {
  return typeof message === "string" && message.trim() ? message : "已达到当前套餐配额，请检查套餐用量。";
}

export class AuthConfigurationError extends Error {
  constructor() {
    super(AUTH_ERROR_MESSAGE);
    this.name = "AuthConfigurationError";
  }
}

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL?.trim() || "/api/v1",
  timeout: 0,
});

api.interceptors.request.use((config) => {
  const key = import.meta.env.API_KEY?.trim();
  if (key) config.headers.set("Authorization", `Bearer ${key}`);
  return config;
});

api.interceptors.response.use(
  (response) => {
    if (response.data?.code === 20014) throw new Error(quotaMessage(response.data.msg));
    if (response.data?.code === 20010) throw new AuthConfigurationError();
    return response;
  },
  (error: unknown) => {
    if (axios.isAxiosError(error) && (
      error.response?.data?.code === 20010 || error.response?.status === 401
    )) {
      return Promise.reject(new AuthConfigurationError());
    }
    return Promise.reject(error);
  },
);

export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const body = error.response?.data;
    const requestId = error.response?.headers["x-request-id"];
    const serverMessage = typeof body?.msg === "string" ? body.msg.trim() : "";
    const message = body?.code === 20014
      ? quotaMessage(serverMessage)
      : serverMessage || "无法连接后端服务，请确认服务已启动。";
    return requestId ? `${message}（请求 ID ${requestId}）` : message;
  }
  return error instanceof Error ? error.message : "操作失败，请稍后再试。";
}
