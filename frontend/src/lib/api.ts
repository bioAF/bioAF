import { getToken } from "./auth";
import { expireSession } from "./session";
import {
  isMaintenanceBody,
  reportMaintenance,
  type MaintenanceInfo,
} from "@/lib/maintenance";

const MAINTENANCE_MESSAGE =
  "bioAF is finishing an upgrade. Try again in a minute.";
const UPGRADE_FAILED_MESSAGE =
  "bioAF’s last upgrade failed. An administrator needs to review the failure and retry.";
const RATE_LIMIT_MESSAGE = "Too many requests. Please wait and try again.";
const INVALID_RESPONSE_MESSAGE =
  "The server sent a response bioAF could not read. Try again.";

/** What a request refused during maintenance says: a failed upgrade is not one that is finishing. */
function maintenanceMessage(body: MaintenanceInfo): string {
  return body.reason === "upgrade_failed"
    ? UPGRADE_FAILED_MESSAGE
    : MAINTENANCE_MESSAGE;
}

/**
 * The application API's base URL. An explicitly empty value means the same origin; unset means
 * the local backend. Read at call time so every caller resolves it the same way.
 */
export function apiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

/**
 * The full URL for an application path. Only application-relative paths are accepted, so the
 * authenticated client can never send the session token to another destination.
 */
export function apiUrl(path: string): string {
  if (!path.startsWith("/") || path.startsWith("//")) {
    throw new Error(`Not an application API path: ${JSON.stringify(path)}`);
  }
  return `${apiBaseUrl()}${path}`;
}

export interface ApiErrorExtras {
  /** The parsed error body, for feature wrappers that read nested conflict payloads. */
  body?: unknown;
  /** Seconds from a Retry-After header, when the server sent one. */
  retryAfter?: number | null;
}

export class ApiError extends Error {
  body: unknown;
  retryAfter: number | null;

  constructor(
    public status: number,
    message: string,
    // Machine-readable error code and structured payload from the domain-error
    // envelope ({detail, code, details}), so callers can react programmatically
    // (e.g. offer to drop samples that blocked a pipeline launch).
    public code?: string,
    public details?: Record<string, unknown>,
    extras: ApiErrorExtras = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.body = extras.body;
    this.retryAfter = extras.retryAfter ?? null;
  }
}

/** Whether `error` is a deliberate cancellation rather than a failure. */
export function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: unknown }).name === "AbortError"
  );
}

function abortError(): DOMException {
  return new DOMException("The operation was aborted.", "AbortError");
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw abortError();
}

// Map known structured error codes to a readable sentence.
const ERROR_CODE_MESSAGES: Record<string, string> = {
  review_in_progress:
    "A review is already in progress for this. Wait for it to finish (or refresh) before starting another.",
};

/** The readable message an error body carries, or null when it carries none. */
function readErrorMessage(body: unknown): string | null {
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    return null;
  }
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") {
    return ERROR_CODE_MESSAGES[detail] ?? detail;
  }
  if (Array.isArray(detail)) {
    return detail
      .map((e: { msg?: string }) => e?.msg ?? "Validation error")
      .join("; ");
  }
  // Object-shaped detail (e.g. structured 409 conflicts carry a nested code
  // plus extra fields). Prefer the nested code's friendly message, then the
  // raw nested string, before falling back to the generic message.
  if (detail && typeof detail === "object") {
    const nested = (detail as { detail?: unknown }).detail;
    if (typeof nested === "string") {
      return ERROR_CODE_MESSAGES[nested] ?? nested;
    }
  }
  return null;
}

export function extractErrorMessage(body: { detail?: unknown }): string {
  return readErrorMessage(body) ?? "Request failed";
}

function parseRetryAfter(value: string | null | undefined): number | null {
  if (!value) return null;
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

interface FailedResponse {
  status: number;
  /** The parsed body, or undefined when it was not JSON. */
  body: unknown;
  retryAfter: string | null;
  /** The token the request was sent with: a 401 ends the session only if it is still current. */
  sentToken: string | null;
  /** The first words of the fallback message, e.g. "Request failed". */
  fallback: string;
}

/**
 * The shared status policy for every application request, whatever carried it: session expiry,
 * maintenance, rate limiting, and the readable message of any other failure.
 */
function errorForResponse(failed: FailedResponse): ApiError {
  const { status, body, sentToken, fallback } = failed;
  const retryAfter = parseRetryAfter(failed.retryAfter);

  if (status === 401) {
    expireSession(sentToken);
  }
  if (status === 503 && isMaintenanceBody(body)) {
    reportMaintenance(body);
    return new ApiError(503, maintenanceMessage(body), "maintenance", undefined, {
      body,
      retryAfter,
    });
  }

  const envelope =
    typeof body === "object" && body !== null && !Array.isArray(body)
      ? (body as { code?: unknown; details?: unknown })
      : {};
  const code = typeof envelope.code === "string" ? envelope.code : undefined;
  const details =
    typeof envelope.details === "object" &&
    envelope.details !== null &&
    !Array.isArray(envelope.details)
      ? (envelope.details as Record<string, unknown>)
      : undefined;

  const message =
    readErrorMessage(body) ??
    (status === 401
      ? "Unauthorized"
      : status === 429
        ? RATE_LIMIT_MESSAGE
        : `${fallback} (HTTP ${status}).`);
  return new ApiError(status, message, code, details, { body, retryAfter });
}

function invalidResponse(status: number): ApiError {
  return new ApiError(status, INVALID_RESPONSE_MESSAGE, "invalid_response");
}

// --- fetch adapter -------------------------------------------------------------------------

export interface RequestControls {
  signal?: AbortSignal;
}

interface SendOptions extends RequestControls {
  method?: string;
  /** A JSON request body. Sent only when truthy, as the client always has. */
  json?: unknown;
  /** Sent as JSON's Content-Type even without a body, as the client always has. */
  jsonContentType?: boolean;
  /** A multipart body; the browser sets the boundary. */
  form?: FormData;
  fallback?: string;
}

/** Send an application request and apply the status policy. The body is left unread. */
async function send(path: string, options: SendOptions = {}): Promise<Response> {
  const url = apiUrl(path);
  throwIfAborted(options.signal);
  const sentToken = getToken();
  const headers: Record<string, string> = {};
  if (options.jsonContentType || options.json) {
    headers["Content-Type"] = "application/json";
  }
  if (sentToken) {
    headers["Authorization"] = `Bearer ${sentToken}`;
  }

  const response = await fetch(url, {
    method: options.method ?? "GET",
    headers,
    body: options.form ?? (options.json ? JSON.stringify(options.json) : undefined),
    signal: options.signal,
  });

  if (!response.ok) {
    const body = await readErrorBody(response);
    throw errorForResponse({
      status: response.status,
      body,
      retryAfter: response.headers?.get?.("Retry-After") ?? null,
      sentToken,
      fallback: options.fallback ?? "Request failed",
    });
  }
  return response;
}

/** The parsed error body, or undefined when it is missing or not JSON. Never throws. */
async function readErrorBody(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

async function readJson<T>(response: Response): Promise<T> {
  if (response.status === 204) {
    return undefined as T;
  }
  try {
    return (await response.json()) as T;
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw invalidResponse(response.status);
  }
}

async function fetchJson<T>(path: string, options: SendOptions = {}): Promise<T> {
  const response = await send(path, { ...options, jsonContentType: true });
  return readJson<T>(response);
}

async function uploadFile<T>(
  path: string,
  file: File,
  extraFields?: Record<string, string>,
  controls: RequestControls = {},
): Promise<T> {
  const formData = new FormData();
  formData.append("file", file);
  if (extraFields) {
    for (const [key, value] of Object.entries(extraFields)) {
      formData.append(key, value);
    }
  }
  const response = await send(path, {
    method: "POST",
    form: formData,
    signal: controls.signal,
    fallback: "Upload failed",
  });
  return readJson<T>(response);
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * Whether a failed read is worth repeating: a network failure or a server error. Cancellation,
 * unreadable responses, session expiry, refusals, rate limiting, and maintenance are not.
 */
export function isTransientFailure(error: unknown): boolean {
  if (isAbortError(error)) return false;
  if (error instanceof ApiError) {
    return error.status >= 500 && error.code !== "maintenance";
  }
  // fetch rejects with a TypeError when the network fails.
  return error instanceof TypeError;
}

async function fetchWithRetry<T>(
  path: string,
  retries = 3,
  delayMs = 2000,
  controls: RequestControls = {},
): Promise<T> {
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fetchJson<T>(path, { signal: controls.signal });
    } catch (err) {
      const isLastAttempt = attempt === retries;
      if (isLastAttempt || !isTransientFailure(err)) throw err;
      await sleep(delayMs, controls.signal);
    }
  }
  throw new Error("Unreachable");
}

/** A filename from Content-Disposition, preferring the RFC 5987 `filename*` form. */
export function filenameFromDisposition(header: string | null | undefined): string | null {
  if (!header) return null;
  const extended = header.match(/filename\*\s*=\s*(?:UTF-8|utf-8)?''([^;\n]+)/);
  if (extended) {
    try {
      return decodeURIComponent(extended[1].trim().replace(/^"|"$/g, ""));
    } catch {
      // fall through to the plain form
    }
  }
  const plain = header.match(/filename\s*=\s*"?([^";\n]+)"?/);
  return plain ? plain[1].trim() : null;
}

export interface BlobRequest extends RequestControls {
  method?: "GET" | "POST";
  body?: unknown;
  /** Used when the response does not name the file. */
  filename?: string;
}

export interface BlobResult {
  blob: Blob;
  filename: string;
}

async function fetchBlob(path: string, options: BlobRequest = {}): Promise<BlobResult> {
  const response = await send(path, {
    method: options.method ?? "GET",
    json: options.body,
    signal: options.signal,
    fallback: "Download failed",
  });
  const blob = await response.blob();
  throwIfAborted(options.signal);
  const filename =
    filenameFromDisposition(response.headers?.get?.("Content-Disposition")) ??
    options.filename ??
    "download";
  return { blob, filename };
}

/** Hand a blob to the browser as a download. */
export function saveBlob(blob: Blob, filename: string): void {
  const blobUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(blobUrl);
}

async function downloadFile(
  path: string,
  method: "GET" | "POST" = "GET",
  body?: unknown,
  options: Omit<BlobRequest, "method" | "body"> = {},
): Promise<void> {
  const { blob, filename } = await fetchBlob(path, { ...options, method, body });
  throwIfAborted(options.signal);
  saveBlob(blob, filename);
}

export interface TextRequest extends RequestControls {
  method?: "GET" | "POST";
  body?: unknown;
}

export async function fetchText(path: string, options: TextRequest = {}): Promise<string> {
  const response = await send(path, {
    method: options.method ?? "GET",
    json: options.body,
    signal: options.signal,
  });
  return response.text();
}

export interface StreamRequest extends RequestControls {
  method?: "GET" | "POST";
  body?: unknown;
}

/**
 * An application request whose response is read as a stream (SSE). The status policy applies
 * before the stream opens; the body is returned unread.
 */
function openStream(path: string, options: StreamRequest = {}): Promise<Response> {
  return send(path, {
    method: options.method ?? "GET",
    json: options.body,
    jsonContentType: options.body !== undefined,
    signal: options.signal,
  });
}

// --- XHR adapter (upload progress) ---------------------------------------------------------

interface SignedUploadOptions extends RequestControls {
  projectId?: number;
  experimentId?: number;
  sampleId?: number;
  isGlobal?: boolean;
  filename?: string;
  onProgress?: (pct: number) => void;
}

interface XhrOptions extends RequestControls {
  method: string;
  url: string;
  headers?: Record<string, string>;
  body: XMLHttpRequestBodyInit;
  onProgress?: (pct: number) => void;
}

interface XhrResult {
  status: number;
  responseText: string;
  retryAfter: string | null;
}

/**
 * Send with XMLHttpRequest for upload progress. Settles once: on load, on network failure, or as
 * a cancellation when the signal aborts, after which no handler fires.
 */
function sendXhr(options: XhrOptions): Promise<XhrResult> {
  return new Promise((resolve, reject) => {
    if (options.signal?.aborted) {
      reject(abortError());
      return;
    }
    const xhr = new XMLHttpRequest();
    const detach = () => {
      xhr.upload.onprogress = null;
      xhr.onload = null;
      xhr.onerror = null;
      xhr.onabort = null;
      options.signal?.removeEventListener("abort", onAbort);
    };
    const onAbort = () => {
      detach();
      xhr.abort();
      reject(abortError());
    };

    xhr.open(options.method, options.url);
    for (const [name, value] of Object.entries(options.headers ?? {})) {
      xhr.setRequestHeader(name, value);
    }
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && options.onProgress) {
        options.onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };
    xhr.onload = () => {
      detach();
      resolve({
        status: xhr.status,
        responseText: xhr.responseText,
        retryAfter: xhr.getResponseHeader?.("Retry-After") ?? null,
      });
    };
    xhr.onerror = () => {
      detach();
      reject(new TypeError("Network error during upload"));
    };
    xhr.onabort = () => {
      detach();
      reject(abortError());
    };
    options.signal?.addEventListener("abort", onAbort, { once: true });
    xhr.send(options.body);
  });
}

function parseJsonText(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

// Upload a file via the initiate → PUT to GCS → complete flow.
// The file bytes never transit the backend, so there is no size limit
// beyond what GCS supports. Progress events are available via XHR.
async function uploadFileSigned<T>(
  file: File,
  options: SignedUploadOptions = {},
): Promise<T> {
  // Step 1: get a signed URL and upload_id from the backend
  const initiateBody: Record<string, unknown> = {
    filename: options.filename ?? file.name,
    expected_size_bytes: file.size,
  };
  if (options.projectId != null) {
    initiateBody.project_id = options.projectId;
  }
  if (options.experimentId != null) {
    initiateBody.experiment_id = options.experimentId;
  }
  if (options.sampleId != null) {
    initiateBody.sample_ids = [options.sampleId];
  }
  if (options.isGlobal) {
    initiateBody.is_global = true;
  }
  const { upload_id, signed_url } = await fetchJson<{
    upload_id: string;
    signed_url: string;
    gcs_uri: string;
    storage_uri: string;
  }>("/api/files/upload/initiate", {
    method: "POST",
    json: initiateBody,
    signal: options.signal,
  });

  // Step 2: PUT directly to storage. The signed URL is the credential: no bioAF session token is
  // sent, and the storage service's answer never ends the bioAF session.
  const put = await sendXhr({
    method: "PUT",
    url: signed_url,
    headers: { "Content-Type": "application/octet-stream" },
    body: file,
    onProgress: options.onProgress,
    signal: options.signal,
  });
  if (put.status < 200 || put.status >= 300) {
    throw new ApiError(put.status, `GCS upload failed: ${put.status}`);
  }

  // Step 3: confirm with the backend so it creates the DB record
  return fetchJson<T>("/api/files/upload/complete", {
    method: "POST",
    json: { upload_id },
    signal: options.signal,
  });
}

// Server-proxied upload: the file bytes transit the backend (POST multipart to
// /api/files/upload/simple), which streams them to storage via the adapter. This
// is the fallback when the active storage backend cannot mint signed URLs (e.g.
// NFS, signed_url_upload=False). Same options as uploadFileSigned.
async function uploadFileProxied<T>(
  file: File,
  options: SignedUploadOptions = {},
): Promise<T> {
  const params = new URLSearchParams();
  if (options.projectId != null)
    params.set("project_id", String(options.projectId));
  if (options.experimentId != null)
    params.set("experiment_id", String(options.experimentId));
  if (options.sampleId != null)
    params.set("sample_ids", String(options.sampleId));
  if (options.isGlobal) params.set("is_global", "true");
  const qs = params.toString();
  const url = apiUrl(`/api/files/upload/simple${qs ? `?${qs}` : ""}`);

  const sentToken = getToken();
  const upload = options.filename
    ? new File([file], options.filename, { type: file.type })
    : file;
  const formData = new FormData();
  formData.append("file", upload);

  const result = await sendXhr({
    method: "POST",
    url,
    headers: sentToken ? { Authorization: `Bearer ${sentToken}` } : {},
    body: formData,
    onProgress: options.onProgress,
    signal: options.signal,
  });

  if (result.status >= 200 && result.status < 300) {
    if (result.status === 204) return undefined as T;
    const body = parseJsonText(result.responseText);
    if (body === undefined) throw invalidResponse(result.status);
    return body as T;
  }
  throw errorForResponse({
    status: result.status,
    body: parseJsonText(result.responseText),
    retryAfter: result.retryAfter,
    sentToken,
    fallback: "Upload failed",
  });
}

export const api = {
  get: <T>(path: string, controls?: RequestControls) =>
    fetchJson<T>(path, { signal: controls?.signal }),
  getWithRetry: <T>(path: string, retries?: number, controls?: RequestControls) =>
    fetchWithRetry<T>(path, retries, undefined, controls),
  post: <T>(path: string, body?: unknown, controls?: RequestControls) =>
    fetchJson<T>(path, { method: "POST", json: body, signal: controls?.signal }),
  patch: <T>(path: string, body?: unknown, controls?: RequestControls) =>
    fetchJson<T>(path, { method: "PATCH", json: body, signal: controls?.signal }),
  put: <T>(path: string, body?: unknown, controls?: RequestControls) =>
    fetchJson<T>(path, { method: "PUT", json: body, signal: controls?.signal }),
  delete: <T>(path: string, controls?: RequestControls) =>
    fetchJson<T>(path, { method: "DELETE", signal: controls?.signal }),
  upload: <T>(
    path: string,
    file: File,
    extraFields?: Record<string, string>,
    controls?: RequestControls,
  ) => uploadFile<T>(path, file, extraFields, controls),
  uploadSigned: <T>(file: File, options?: SignedUploadOptions) =>
    uploadFileSigned<T>(file, options),
  uploadProxied: <T>(file: File, options?: SignedUploadOptions) =>
    uploadFileProxied<T>(file, options),
  download: (
    path: string,
    method?: "GET" | "POST",
    body?: unknown,
    options?: Omit<BlobRequest, "method" | "body">,
  ) => downloadFile(path, method, body, options),
  /** An authenticated binary response, with the filename the server gave it. */
  blob: (path: string, options?: BlobRequest) => fetchBlob(path, options),
  /** An authenticated text response (reports, citations). */
  text: (path: string, options?: TextRequest) => fetchText(path, options),
  /** An authenticated streaming response, checked before it opens and returned unread. */
  stream: (path: string, options?: StreamRequest) => openStream(path, options),
};

/**
 * Fetch a short-lived content token for inline resource display.
 * These tokens expire in 60 seconds, are scoped to a single resource,
 * and carry no user identity -- safe to put in URL query params.
 */
async function fetchContentToken(
  resourceType: "file" | "plot_thumbnail",
  resourceId: number,
): Promise<string> {
  const data = await fetchJson<{ token: string; expires_in: number }>(
    "/api/content-tokens",
    {
      method: "POST",
      json: {
        resource_type: resourceType,
        resource_id: resourceId,
      },
    },
  );
  return data.token;
}

/**
 * Build a direct URL for inline file content (images, previews).
 * Fetches a short-lived content token instead of embedding the
 * session JWT in the URL.
 */
export async function fileContentUrl(fileId: number): Promise<string> {
  const base = apiUrl(`/api/files/${fileId}/content`);
  try {
    const token = await fetchContentToken("file", fileId);
    return `${base}?token=${encodeURIComponent(token)}`;
  } catch {
    return base;
  }
}

export async function plotThumbnailContentUrl(plotId: number): Promise<string> {
  const base = apiUrl(`/api/plots/${plotId}/thumbnail/content`);
  try {
    const token = await fetchContentToken("plot_thumbnail", plotId);
    return `${base}?token=${encodeURIComponent(token)}`;
  } catch {
    return base;
  }
}
