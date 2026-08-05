"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { LabDocumentViewer } from "@/components/lab-knowledge/LabDocumentViewer";
import { api } from "@/lib/api";
import { getCurrentUser } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { labDocuments, uploadDocumentFile, type LabDocumentNote } from "@/lib/labDocuments";

interface Tag {
  id: number;
  name: string;
}
interface UserSummary {
  id: number;
  name: string | null;
  email: string;
}
interface LabDocument {
  id: number;
  title: string;
  description: string | null;
  file_name: string;
  current_version: number;
  file_size_bytes: number | null;
  mime_type: string | null;
  is_archived: boolean;
  tags: Tag[];
  created_by: UserSummary | null;
  created_at: string;
  updated_at: string;
}
interface DocVersion {
  version_number: number;
  file_name: string;
  change_note: string | null;
  uploaded_by: UserSummary | null;
  uploaded_at: string;
}

const API_BASE = "/api/lab-knowledge";

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function LabDocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const documentId = Number(params.id);
  const user = getCurrentUser();
  const { canAccess } = usePermissions();
  const canManage = canAccess("lab_documents", "manage");

  const [doc, setDoc] = useState<LabDocument | null>(null);
  const [versions, setVersions] = useState<DocVersion[]>([]);
  const [notes, setNotes] = useState<LabDocumentNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [noteBody, setNoteBody] = useState("");
  const [postingNote, setPostingNote] = useState(false);
  const newVersionInput = useRef<HTMLInputElement>(null);
  const [uploadingVersion, setUploadingVersion] = useState(false);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [d, v, n] = await Promise.all([
        api.get<LabDocument>(`${API_BASE}/documents/${documentId}`),
        api.get<DocVersion[]>(`${API_BASE}/documents/${documentId}/versions`).catch(() => []),
        labDocuments.listNotes(documentId).catch(() => []),
      ]);
      setDoc(d);
      setVersions(v);
      setNotes(n);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load document");
    } finally {
      setLoading(false);
    }
  }, [documentId]);

  useEffect(() => {
    refresh();
  }, [refresh, router]);

  const download = async (version: number) => {
    const { download_url } = await api.get<{ download_url: string }>(
      `${API_BASE}/documents/${documentId}/download?version=${version}`,
    );
    window.open(download_url, "_blank");
  };

  const archive = async () => {
    if (!doc) return;
    await api.post(`${API_BASE}/documents/${documentId}/archive?archived=${!doc.is_archived}`);
    refresh();
  };

  const uploadNewVersion = async () => {
    const file = newVersionInput.current?.files?.[0];
    if (!file) return;
    setUploadingVersion(true);
    try {
      const uploadToken = await uploadDocumentFile(file);
      await api.post(`${API_BASE}/documents/${documentId}/versions`, { upload_token: uploadToken });
      if (newVersionInput.current) newVersionInput.current.value = "";
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploadingVersion(false);
    }
  };

  const submitNote = async () => {
    if (!noteBody.trim()) return;
    setPostingNote(true);
    try {
      await labDocuments.addNote(documentId, noteBody.trim());
      setNoteBody("");
      const n = await labDocuments.listNotes(documentId);
      setNotes(n);
    } finally {
      setPostingNote(false);
    }
  };

  const deleteNote = async (noteId: number) => {
    if (!confirm("Delete this note?")) return;
    await labDocuments.deleteNote(documentId, noteId);
    setNotes(await labDocuments.listNotes(documentId));
  };

  if (loading) {
    return (
      <main className="flex flex-1 items-center justify-center p-6">
        <LoadingSpinner />
      </main>
    );
  }
  if (!doc) {
    return (
      <main className="p-6">
        <div className="text-gray-600">Document not found.</div>
        {error && <div className="text-red-700">{error}</div>}
      </main>
    );
  }

  const visibleNotes = notes.filter((n) => !n.deleted);

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <button
        onClick={() => router.push("/lab-knowledge/documents")}
        className="text-bioaf-700 hover:underline text-sm mb-4"
      >
        ← Back to documents
      </button>
      <h1 className="text-2xl font-bold mb-1">
        {doc.title}
        {doc.is_archived && (
          <span className="ml-2 text-sm font-normal text-gray-500">(archived)</span>
        )}
      </h1>
      <div className="text-gray-600 mb-4 text-sm">
        {doc.file_name} · v{doc.current_version} ·{" "}
        {doc.created_by?.name ?? doc.created_by?.email ?? "Unknown"} · {fmtDate(doc.updated_at)}
      </div>
      {doc.description && <p className="text-sm text-gray-700 mb-4">{doc.description}</p>}
      {error && <div className="text-red-600 text-sm mb-3">{error}</div>}

      <div className="grid md:grid-cols-3 gap-6">
        <section className="md:col-span-2 space-y-6">
          <div className="bg-white rounded shadow p-4">
            <h2 className="font-semibold mb-2">Document</h2>
            <LabDocumentViewer
              documentId={doc.id}
              version={doc.current_version}
              mimeType={doc.mime_type}
              fileName={doc.file_name}
            />
          </div>

          <div className="bg-white rounded shadow p-4">
            <h2 className="font-semibold mb-2">Notes ({visibleNotes.length})</h2>
            <div className="mb-4">
              <textarea
                value={noteBody}
                onChange={(e) => setNoteBody(e.target.value)}
                rows={3}
                placeholder="Add a note"
                aria-label="Add a note"
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              />
              <div className="flex justify-end mt-1">
                <button
                  onClick={submitNote}
                  disabled={!noteBody.trim() || postingNote}
                  className="bg-bioaf-600 text-white px-3 py-1 rounded text-sm disabled:opacity-50"
                >
                  {postingNote ? "Posting..." : "Post"}
                </button>
              </div>
            </div>
            {visibleNotes.length === 0 ? (
              <div className="text-sm text-gray-500">No notes yet.</div>
            ) : (
              <ul className="space-y-3">
                {visibleNotes.map((n) => (
                  <li key={n.id} className="border-l-2 border-gray-200 pl-3">
                    <div className="flex justify-between items-center mb-1">
                      <div className="text-xs text-gray-500">
                        <span className="font-medium text-gray-700">
                          {n.user?.name ?? n.user?.email ?? "Unknown"}
                        </span>
                        <span className="ml-2">{fmtDate(n.created_at)}</span>
                      </div>
                      {(n.user?.id === user?.id || canManage) && (
                        <button
                          onClick={() => deleteNote(n.id)}
                          className="text-red-700 text-xs hover:underline"
                        >
                          delete
                        </button>
                      )}
                    </div>
                    <span className="text-sm whitespace-pre-wrap">{n.body}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>

        <aside className="space-y-4">
          <div className="bg-white rounded shadow p-4">
            <h3 className="font-semibold mb-2">Details</h3>
            <dl className="text-sm space-y-1">
              <dt className="text-gray-500">Tags</dt>
              <dd>{doc.tags.map((t) => t.name).join(", ") || "None"}</dd>
              <dt className="text-gray-500 mt-2">Type</dt>
              <dd>{doc.mime_type ?? "unknown"}</dd>
            </dl>
          </div>

          <div className="bg-white rounded shadow p-4">
            <h3 className="font-semibold mb-2">Version history</h3>
            <ul className="space-y-2">
              {versions.map((v) => (
                <li key={v.version_number} className="text-sm flex items-center justify-between">
                  <span>
                    v{v.version_number} {v.change_note ? `- ${v.change_note}` : ""}{" "}
                    <span className="text-gray-500">({fmtDate(v.uploaded_at)})</span>
                  </span>
                  <button
                    onClick={() => download(v.version_number)}
                    className="text-bioaf-700 text-xs hover:underline"
                  >
                    Download
                  </button>
                </li>
              ))}
            </ul>
            {canManage && (
              <div className="border-t pt-3 mt-3 space-y-2">
                <label className="block text-sm font-medium">Upload new version</label>
                <input
                  ref={newVersionInput}
                  type="file"
                  aria-label="New version file"
                  className="text-sm"
                />
                <button
                  onClick={uploadNewVersion}
                  disabled={uploadingVersion}
                  className="bg-bioaf-600 text-white text-xs rounded px-3 py-1 disabled:opacity-50"
                >
                  {uploadingVersion ? "Uploading..." : "Upload"}
                </button>
              </div>
            )}
          </div>

          {canManage && (
            <div className="bg-white rounded shadow p-4">
              <h3 className="font-semibold mb-2">Manage</h3>
              <button onClick={archive} className="text-sm text-amber-700 hover:underline">
                {doc.is_archived ? "Restore document" : "Archive document"}
              </button>
            </div>
          )}
        </aside>
      </div>
    </main>
  );
}
