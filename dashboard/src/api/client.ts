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

const REQUEST_TIMEOUT_MS = 20_000;

function flattenMessage(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(flattenMessage).join(" ");
  if (typeof value === "object" && value !== null) {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, nested]) => `${key}: ${flattenMessage(nested)}`)
      .join("; ");
  }
  return String(value);
}

function normalizeError(status: number, body: unknown): ApiError {
  if (Array.isArray(body)) {
    return new ApiError(status, body.map(flattenMessage).join(" "));
  }
  if (typeof body === "object" && body !== null) {
    const record = body as Record<string, unknown>;
    if (typeof record.detail === "string") {
      return new ApiError(status, record.detail);
    }
    const fieldErrors: FieldErrors = {};
    const summaries: string[] = [];
    for (const [field, value] of Object.entries(record)) {
      const messages = (Array.isArray(value) ? value : [value]).map(flattenMessage);
      fieldErrors[field] = messages;
      // non_field_errors is DRF plumbing, not a name a health worker knows.
      const label = field === "non_field_errors" ? "" : `${field}: `;
      summaries.push(`${label}${messages.join(" ")}`);
    }
    if (summaries.length > 0) {
      return new ApiError(status, summaries.join("; "), fieldErrors);
    }
  }
  const fallback: Record<number, string> = {
    401: "Your session has expired. Sign in again.",
    403: "Your role does not permit this action.",
    404: "This record does not exist, or is outside your scope.",
    409: "The record has moved on since you loaded it. Check its current state.",
    429: "Too many requests. Wait a moment and try again.",
  };
  return new ApiError(status, fallback[status] ?? `The server returned ${status}.`);
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = tokenProvider();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  } else if (method !== "GET") {
    // Session mode (development): the Django session cookie authenticates,
    // and Django's CSRF check wants the cookie echoed in a header on every
    // unsafe method.
    const csrf = readCookie("csrftoken");
    if (csrf) headers["X-CSRFToken"] = csrf;
  }
  if (body !== undefined) headers["Content-Type"] = "application/json";

  // Every call has a ceiling. A hung request would otherwise leave a spinner
  // running forever on the one screen a supervisor checks between patients.
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") {
      throw new ApiError(0, "The server did not respond in time. Try again.");
    }
    throw new ApiError(0, "Could not reach the server. Check the connection.");
  }

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
