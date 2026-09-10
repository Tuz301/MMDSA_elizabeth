/**
 * The API client.
 *
 * One fetch wrapper, one error shape. Every DRF error body — a {detail}
 * string, a field map, or a non-JSON body — is normalised into ApiError so a
 * form can show the message beside the field it belongs to and a page can
 * show the summary without caring which shape the server chose.
 */

export interface FieldErrors {
  [field: string]: string[];
}

export class ApiError extends Error {
  readonly status: number;
  readonly fieldErrors: FieldErrors;

  constructor(status: number, message: string, fieldErrors: FieldErrors = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

export type TokenProvider = () => string | null;

let tokenProvider: TokenProvider = () => null;
let onUnauthorized: () => void = () => {};

export function configureClient(options: {
  tokenProvider: TokenProvider;
  onUnauthorized: () => void;
}): void {
  tokenProvider = options.tokenProvider;
  onUnauthorized = options.onUnauthorized;
}

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

function normalizeError(status: number, body: unknown): ApiError {
  if (typeof body === "object" && body !== null) {
    const record = body as Record<string, unknown>;
    if (typeof record.detail === "string") {
      return new ApiError(status, record.detail);
    }
    const fieldErrors: FieldErrors = {};
    const summaries: string[] = [];
    for (const [field, value] of Object.entries(record)) {
      const messages = (Array.isArray(value) ? value : [value]).map(String);
      fieldErrors[field] = messages;
      summaries.push(`${field}: ${messages.join(" ")}`);
    }
    if (summaries.length > 0) {
      return new ApiError(status, summaries.join("; "), fieldErrors);
    }
  }
  const fallback: Record<number, string> = {
    401: "Your session has expired. Sign in again.",
    403: "Your role does not permit this action.",
    404: "This record does not exist, or is outside your scope.",
    409: "The record has moved on since you loaded it. It has been refreshed.",
    429: "Too many requests. Wait a moment and try again.",
  };
  return new ApiError(status, fallback[status] ?? `The server returned ${status}.`);
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = tokenProvider();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const response = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 401) {
    onUnauthorized();
  }
  if (response.status === 204) {
    return undefined as T;
  }

  let parsed: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }

  if (!response.ok) {
    throw normalizeError(response.status, parsed);
  }
  return parsed as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
};

/** Build a query string, dropping empty values so filters compose cleanly. */
export function query(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}
