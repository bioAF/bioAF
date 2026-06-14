import { api } from "./api";
import { getToken } from "./auth";
import { uploadFileResumable } from "./resumableUpload";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const BASE = "/api/lab-knowledge";

interface UploadUrlResponse {
  upload_token: string;
  signed_url: string;
  gcs_uri: string;
  storage_uri: string;
}

// Origin-aware resumable upload to GCS (fixes the cross-origin "Failed to fetch"
// that a plain signed PUT hit). Returns the token the create/version endpoints
// finalize against.
export async function uploadDocumentFile(file: File): Promise<string> {
  const init = await api.post<UploadUrlResponse>(`${BASE}/documents/upload-url`, {
    file_name: file.name,
    mime_type: file.type || null,
    size_bytes: file.size,
  });
  await uploadFileResumable(init.signed_url, file);
  return init.upload_token;
}

export interface LabDocumentNote {
  id: number;
  body: string;
  user: { id: number; name: string | null; email: string } | null;
  created_at: string;
  deleted: boolean;
}

// The content endpoint requires the Authorization header, so a plain <a href>
// would 401. Route the bytes through fetch (auth header) and hand the resulting
// blob to the inline viewer, mirroring fetchPaperPdfBlob for literature papers.
export async function fetchLabDocumentBlob(documentId: number, version?: number): Promise<Blob> {
  const q = version ? `?version=${version}` : "";
  const resp = await fetch(`${API_URL}${BASE}/documents/${documentId}/content${q}`, {
    headers: { Authorization: `Bearer ${getToken() ?? ""}` },
  });
  if (!resp.ok) {
    throw new Error(resp.status === 404 ? "Document not found." : "Could not load the document.");
  }
  return resp.blob();
}

export const labDocuments = {
  listNotes: (documentId: number) =>
    api.get<LabDocumentNote[]>(`${BASE}/documents/${documentId}/notes`),
  addNote: (documentId: number, body: string) =>
    api.post<LabDocumentNote>(`${BASE}/documents/${documentId}/notes`, { body }),
  deleteNote: (documentId: number, noteId: number) =>
    api.delete(`${BASE}/documents/${documentId}/notes/${noteId}`),
};
