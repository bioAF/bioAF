import { getToken, removeToken } from "./auth";
import { clearPermissionsCache } from "@/hooks/usePermissions";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function fetchApi<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    removeToken();
    clearPermissionsCache();
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    throw new ApiError(401, "Unauthorized");
  }

  if (response.status === 429) {
    throw new ApiError(429, "Too many requests. Please wait and try again.");
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Unknown error" }));
    throw new ApiError(response.status, error.detail || "Request failed");
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}

async function uploadFile<T>(path: string, file: File, extraFields?: Record<string, string>): Promise<T> {
  const token = getToken();
  const formData = new FormData();
  formData.append("file", file);
  if (extraFields) {
    for (const [key, value] of Object.entries(extraFields)) {
      formData.append(key, value);
    }
  }

  const headers: Record<string, string> = {};
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers,
    body: formData,
  });

  if (response.status === 401) {
    removeToken();
    clearPermissionsCache();
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    throw new ApiError(401, "Unauthorized");
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Unknown error" }));
    throw new ApiError(response.status, error.detail || "Request failed");
  }

  return response.json();
}

interface SignedUploadOptions {
  projectId?: number;
  experimentId?: number;
  sampleId?: number;
  filename?: string;
  onProgress?: (pct: number) => void;
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
  const { upload_id, signed_url } = await fetchApi<{
    upload_id: string;
    signed_url: string;
    gcs_uri: string;
  }>("/api/files/upload/initiate", {
    method: "POST",
    body: JSON.stringify(initiateBody),
  });

  // Step 2: PUT directly to GCS via XHR so we get upload progress events
  await new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", signed_url);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && options.onProgress) {
        options.onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new ApiError(xhr.status, `GCS upload failed: ${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.send(file);
  });

  // Step 3: confirm with the backend so it creates the DB record
  return fetchApi<T>("/api/files/upload/complete", {
    method: "POST",
    body: JSON.stringify({ upload_id }),
  });
}

async function fetchWithRetry<T>(
  path: string,
  options: RequestInit = {},
  retries = 3,
  delayMs = 2000,
): Promise<T> {
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fetchApi<T>(path, options);
    } catch (err) {
      const isLastAttempt = attempt === retries;
      const isRetryable =
        !(err instanceof ApiError) ||
        err.status >= 500;
      if (isLastAttempt || !isRetryable) throw err;
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
  throw new Error("Unreachable");
}

async function downloadFile(
  path: string,
  method: "GET" | "POST" = "GET",
  body?: unknown,
): Promise<void> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  if (body) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(`${API_URL}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (response.status === 401) {
    removeToken();
    clearPermissionsCache();
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    throw new ApiError(401, "Unauthorized");
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Download failed" }));
    throw new ApiError(response.status, error.detail || "Download failed");
  }

  const blob = await response.blob();
  const blobUrl = URL.createObjectURL(blob);

  const contentDisposition = response.headers.get("Content-Disposition");
  let filename = "download";
  if (contentDisposition) {
    const match = contentDisposition.match(/filename="?([^";\n]+)"?/);
    if (match) {
      filename = match[1];
    }
  }

  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(blobUrl);
}

export const api = {
  get: <T>(path: string) => fetchApi<T>(path),
  getWithRetry: <T>(path: string, retries?: number) =>
    fetchWithRetry<T>(path, {}, retries),
  post: <T>(path: string, body?: unknown) =>
    fetchApi<T>(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),
  patch: <T>(path: string, body?: unknown) =>
    fetchApi<T>(path, {
      method: "PATCH",
      body: body ? JSON.stringify(body) : undefined,
    }),
  put: <T>(path: string, body?: unknown) =>
    fetchApi<T>(path, {
      method: "PUT",
      body: body ? JSON.stringify(body) : undefined,
    }),
  delete: <T>(path: string) =>
    fetchApi<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, file: File, extraFields?: Record<string, string>) =>
    uploadFile<T>(path, file, extraFields),
  uploadSigned: <T>(file: File, options?: SignedUploadOptions) =>
    uploadFileSigned<T>(file, options),
  download: (path: string, method?: "GET" | "POST", body?: unknown) =>
    downloadFile(path, method, body),
};

/**
 * Build a direct URL for inline file content (images, previews).
 * Uses the /content endpoint which proxies bytes without audit logging,
 * unlike /download which creates an audit entry per call.
 */
export function fileContentUrl(fileId: number): string {
  const token = getToken();
  const base = `${API_URL}/api/files/${fileId}/content`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}

export { ApiError };
