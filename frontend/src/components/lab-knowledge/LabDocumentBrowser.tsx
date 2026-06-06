"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { uploadFileResumable } from "@/lib/resumableUpload";
import { usePermissions } from "@/hooks/usePermissions";

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
  md5_checksum: string | null;
  is_archived: boolean;
  tags: Tag[];
  created_by: UserSummary | null;
  created_at: string;
  updated_at: string;
}

interface DocVersion {
  version_number: number;
  file_name: string;
  file_size_bytes: number | null;
  md5_checksum: string | null;
  change_note: string | null;
  uploaded_by: UserSummary | null;
  uploaded_at: string;
}

interface ListResponse {
  documents: LabDocument[];
  total: number;
  page: number;
  page_size: number;
}

interface UploadUrlResponse {
  upload_token: string;
  signed_url: string;
  gcs_uri: string;
}

const API_BASE = "/api/lab-knowledge";

// Direct-to-GCS upload via the resumable session URL the server returns. Sending
// the file size up front lets the server scope the session; the resumable PUT is
// origin-aware so the cross-origin upload is accepted (fixes "Failed to fetch").
async function uploadToGcs(file: File): Promise<string> {
  const init = await api.post<UploadUrlResponse>(`${API_BASE}/documents/upload-url`, {
    file_name: file.name,
    mime_type: file.type || null,
    size_bytes: file.size,
  });
  await uploadFileResumable(init.signed_url, file);
  return init.upload_token;
}

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

export function LabDocumentBrowser({ focusDocId }: { focusDocId?: number }) {
  const { canAccess } = usePermissions();
  const canManage = canAccess("lab_documents", "manage");
  const canManageTags = canAccess("lab_document_tags", "manage");

  const [documents, setDocuments] = useState<LabDocument[]>([]);
  const [tags, setTags] = useState<Tag[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedTagIds, setSelectedTagIds] = useState<number[]>([]);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [query, setQuery] = useState("");

  const [selected, setSelected] = useState<LabDocument | null>(null);
  const [showUpload, setShowUpload] = useState(false);

  const fetchTags = useCallback(async () => {
    try {
      const data = await api.get<Tag[]>(`${API_BASE}/document-tags`);
      setTags(data);
    } catch {
      /* tags are non-critical for the list */
    }
  }, []);

  const fetchDocuments = useCallback(async () => {
    setLoading(true);
    setError(null);
    const params = new URLSearchParams();
    for (const id of selectedTagIds) params.append("tag_ids", String(id));
    if (includeArchived) params.set("include_archived", "true");
    if (query.trim()) params.set("q", query.trim());
    try {
      const data = await api.get<ListResponse>(`${API_BASE}/documents?${params.toString()}`);
      setDocuments(data.documents);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load documents");
    } finally {
      setLoading(false);
    }
  }, [selectedTagIds, includeArchived, query]);

  useEffect(() => {
    fetchTags();
  }, [fetchTags]);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

  useEffect(() => {
    if (focusDocId && documents.length) {
      const match = documents.find((d) => d.id === focusDocId);
      if (match) setSelected(match);
    }
  }, [focusDocId, documents]);

  const toggleTag = (id: number) => {
    setSelectedTagIds((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]));
  };

  if (loading) {
    return <div data-testid="lab-docs-loading" className="p-8 text-gray-500">Loading documents...</div>;
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <input
            type="search"
            placeholder="Search documents..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="border rounded px-3 py-1.5 text-sm w-64"
            aria-label="Search documents"
          />
          <label className="flex items-center gap-1.5 text-sm text-gray-600">
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={(e) => setIncludeArchived(e.target.checked)}
            />
            Show archived
          </label>
        </div>
        {canManage && (
          <button
            type="button"
            onClick={() => setShowUpload(true)}
            className="bg-blue-600 text-white rounded px-4 py-1.5 text-sm font-medium"
          >
            Upload Document
          </button>
        )}
      </div>

      {tags.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-4">
          {tags.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => toggleTag(t.id)}
              className={`text-xs rounded-full px-3 py-1 border ${
                selectedTagIds.includes(t.id)
                  ? "bg-blue-600 text-white border-blue-600"
                  : "bg-white text-gray-700 border-gray-300"
              }`}
            >
              {t.name}
            </button>
          ))}
        </div>
      )}

      {error && <div className="text-red-600 text-sm mb-3">{error}</div>}

      {documents.length === 0 ? (
        <div className="text-gray-500 py-12 text-center">No documents found.</div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b">
              <th className="py-2">Title</th>
              <th>Tags</th>
              <th>Version</th>
              <th>Uploaded by</th>
              <th>Last updated</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((d) => (
              <tr
                key={d.id}
                onClick={() => setSelected(d)}
                className="border-b hover:bg-gray-50 cursor-pointer"
              >
                <td className="py-2 font-medium">
                  {d.title}
                  {d.is_archived && <span className="ml-2 text-xs text-gray-400">(archived)</span>}
                </td>
                <td className="text-gray-600">{d.tags.map((t) => t.name).join(", ")}</td>
                <td>v{d.current_version}</td>
                <td className="text-gray-600">{d.created_by?.name ?? d.created_by?.email ?? "-"}</td>
                <td className="text-gray-600">{fmtDate(d.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {selected && (
        <DocumentDetailPanel
          doc={selected}
          canManage={canManage}
          tags={tags}
          onClose={() => setSelected(null)}
          onChanged={() => {
            setSelected(null);
            fetchDocuments();
          }}
        />
      )}

      {showUpload && (
        <UploadDocumentModal
          tags={tags}
          canManageTags={canManageTags}
          onClose={() => setShowUpload(false)}
          onUploaded={() => {
            setShowUpload(false);
            fetchTags();
            fetchDocuments();
          }}
        />
      )}
    </div>
  );
}

function DocumentDetailPanel({
  doc,
  canManage,
  tags,
  onClose,
  onChanged,
}: {
  doc: LabDocument;
  canManage: boolean;
  tags: Tag[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [versions, setVersions] = useState<DocVersion[]>([]);
  const newVersionInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api
      .get<DocVersion[]>(`${API_BASE}/documents/${doc.id}/versions`)
      .then(setVersions)
      .catch(() => setVersions([]));
  }, [doc.id]);

  const download = async (version: number) => {
    const { download_url } = await api.get<{ download_url: string }>(
      `${API_BASE}/documents/${doc.id}/download?version=${version}`,
    );
    window.open(download_url, "_blank");
  };

  const archive = async () => {
    await api.post(`${API_BASE}/documents/${doc.id}/archive?archived=${!doc.is_archived}`);
    onChanged();
  };

  const uploadNewVersion = async () => {
    const file = newVersionInput.current?.files?.[0];
    if (!file) return;
    const uploadToken = await uploadToGcs(file);
    await api.post(`${API_BASE}/documents/${doc.id}/versions`, { upload_token: uploadToken });
    onChanged();
  };

  return (
    <div className="fixed inset-0 bg-black/30 flex justify-end z-50" onClick={onClose}>
      <div className="bg-white w-[28rem] h-full overflow-y-auto p-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-4">
          <h2 className="text-xl font-bold">{doc.title}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="text-gray-400">
            x
          </button>
        </div>
        {doc.description && <p className="text-sm text-gray-600 mb-3">{doc.description}</p>}
        <div className="text-xs text-gray-500 mb-4">
          {doc.tags.map((t) => t.name).join(", ") || "No tags"}
        </div>

        <h3 className="font-semibold text-sm mb-2">Version history</h3>
        <ul className="space-y-2 mb-4">
          {versions.map((v) => (
            <li key={v.version_number} className="text-sm flex items-center justify-between">
              <span>
                v{v.version_number} {v.change_note ? `- ${v.change_note}` : ""}{" "}
                <span className="text-gray-400">({fmtDate(v.uploaded_at)})</span>
              </span>
              <button
                type="button"
                onClick={() => download(v.version_number)}
                className="text-blue-600 text-xs"
              >
                Download
              </button>
            </li>
          ))}
        </ul>

        {canManage && (
          <div className="border-t pt-4 space-y-3">
            <div>
              <label className="block text-sm font-medium mb-1">Upload new version</label>
              <input ref={newVersionInput} type="file" aria-label="New version file" className="text-sm" />
              <button
                type="button"
                onClick={uploadNewVersion}
                className="ml-2 bg-blue-600 text-white text-xs rounded px-3 py-1"
              >
                Upload
              </button>
            </div>
            <button type="button" onClick={archive} className="text-sm text-amber-700">
              {doc.is_archived ? "Restore document" : "Archive document"}
            </button>
          </div>
        )}
        {/* tags list available for future inline editing */}
        <input type="hidden" data-tag-count={tags.length} />
      </div>
    </div>
  );
}

function UploadDocumentModal({
  tags,
  canManageTags,
  onClose,
  onUploaded,
}: {
  tags: Tag[];
  canManageTags: boolean;
  onClose: () => void;
  onUploaded: () => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<"device" | "url">("device");
  const [sourceUrl, setSourceUrl] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tagIds, setTagIds] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const toggle = (id: number) =>
    setTagIds((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]));

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      if (mode === "url") {
        if (!sourceUrl.trim()) {
          setErr("Enter a URL to import from.");
          setBusy(false);
          return;
        }
        await api.post(`${API_BASE}/documents/import-url`, {
          url: sourceUrl.trim(),
          title: title || null,
          description: description || null,
          tag_ids: tagIds,
        });
        onUploaded();
        return;
      }
      const file = fileInput.current?.files?.[0];
      if (!file) {
        setErr("Choose a file to upload.");
        setBusy(false);
        return;
      }
      const uploadToken = await uploadToGcs(file);
      await api.post(`${API_BASE}/documents`, {
        upload_token: uploadToken,
        title: title || file.name,
        description: description || null,
        tag_ids: tagIds,
      });
      onUploaded();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Upload failed");
      setBusy(false);
    }
  };

  const modeButton = (value: "device" | "url", label: string, extra = "") => (
    <button
      type="button"
      onClick={() => {
        setMode(value);
        setErr(null);
      }}
      aria-pressed={mode === value}
      className={`px-3 py-1.5 text-sm ${extra} ${
        mode === value ? "bg-blue-600 text-white" : "bg-white text-gray-700 hover:bg-gray-50"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-lg w-[30rem] p-6" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-bold mb-4">Upload Document</h2>
        <div className="space-y-3">
          <div className="inline-flex rounded-md border border-gray-300 overflow-hidden">
            {modeButton("device", "From device")}
            {modeButton("url", "From URL", "border-l border-gray-300")}
          </div>
          {mode === "device" ? (
            <input ref={fileInput} type="file" aria-label="Document file" className="text-sm block" />
          ) : (
            <input
              type="url"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder="https://example.com/policy.pdf"
              aria-label="Document URL"
              className="border rounded px-3 py-1.5 text-sm w-full"
            />
          )}
          <input
            type="text"
            placeholder="Title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="border rounded px-3 py-1.5 text-sm w-full"
            aria-label="Title"
          />
          <textarea
            placeholder="Description (optional)"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="border rounded px-3 py-1.5 text-sm w-full"
            aria-label="Description"
          />
          <div className="flex flex-wrap gap-2">
            {tags.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => toggle(t.id)}
                className={`text-xs rounded-full px-3 py-1 border ${
                  tagIds.includes(t.id)
                    ? "bg-blue-600 text-white border-blue-600"
                    : "bg-white text-gray-700 border-gray-300"
                }`}
              >
                {t.name}
              </button>
            ))}
          </div>
          {!canManageTags && tags.length === 0 && (
            <p className="text-xs text-gray-400">No tags available.</p>
          )}
          {err && <div className="text-red-600 text-sm">{err}</div>}
        </div>
        <div className="flex justify-end gap-2 mt-5">
          <button type="button" onClick={onClose} className="text-sm px-3 py-1.5">
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={busy}
            className="bg-blue-600 text-white text-sm rounded px-4 py-1.5 disabled:opacity-50"
          >
            {busy
              ? mode === "url"
                ? "Importing..."
                : "Uploading..."
              : mode === "url"
                ? "Import"
                : "Upload"}
          </button>
        </div>
      </div>
    </div>
  );
}
