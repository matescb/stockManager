export type ApiOk<T> = { data: T; status: { category: string; message: string } };
export type ApiErr = { data: null; status: { category: string; message: string }; errors?: { field: string; message: string }[] };

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  body: ApiErr | null;
  constructor(status: number, body: ApiErr | null, message: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  if (init.body && !(init.body instanceof FormData) && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  const res = await fetch(`${BASE}${path}`, { ...init, headers, credentials: "include" });
  let body: any = null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    body = await res.json();
  }
  if (!res.ok) {
    const msg = body?.status?.message || res.statusText;
    throw new ApiError(res.status, body, msg);
  }
  return body?.data as T;
}

export const api = {
  get: <T>(p: string) => request<T>(p),
  post: <T>(p: string, body?: any) =>
    request<T>(p, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined }),
  patch: <T>(p: string, body?: any) =>
    request<T>(p, { method: "PATCH", body: body !== undefined ? JSON.stringify(body) : undefined }),
  delete: <T>(p: string) => request<T>(p, { method: "DELETE" }),
  upload: <T>(p: string, form: FormData) => request<T>(p, { method: "POST", body: form }),
};
