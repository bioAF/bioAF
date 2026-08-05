"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { uploadDocumentFile } from "@/lib/labDocuments";
import { usePermissions } from "@/hooks/usePermissions";

import { clickableRow } from "@/lib/a11y";
import { useDismissOnEscape } from "@/hooks/useDismissOnEscape";

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

interface ListResponse {
  documents: LabDocument[];
  total: number;
  page: number;
  page_size: number;
}

const API_BASE = "/api/lab-knowledge";

interface UrlImport {
  id: number;
  status: string;
  document_id: number | null;
  error_message: string | null;
}

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

export function LabDocumentBrowser() {
  const router = useRouter();
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

  const toggleTag = (id: number) => {
    setSelectedTagIds((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]));
  };

  // NOTE: deliberately no `if (loading) return ...` early return here. `query` is a
  // dependency of fetchDocuments, so every keystroke sets loading=true; returning
  // early unmounted the whole toolbar, taking the search input (and the caret) with
  // it, so only the first character of a search ever landed. The loading state is
  // rendered in the results region instead, below, and the toolbar stays mounted.
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
            className="bg-bioaf-600 text-white rounded px-4 py-1.5 text-sm font-medium"
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
                  ? "bg-bioaf-600 text-white border-bioaf-600"
                  : "bg-white text-gray-700 border-gray-300"
              }`}
            >
              {t.name}
            </button>
          ))}
        </div>
      )}

      {error && <div className="text-red-600 text-sm mb-3">{error}</div>}

      {loading ? (
        <div data-testid="lab-docs-loading" className="text-gray-500 py-12 text-center">
          Loading documents...
        </div>
      ) : documents.length === 0 ? (
        <div className="text-gray-500 py-12 text-center">No documents found.</div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b">
              <th scope="col" className="py-2">Title</th>
              <th scope="col">Tags</th>
              <th scope="col">Version</th>
              <th scope="col">Uploaded by</th>
              <th scope="col">Last updated</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((d) => (
              <tr
                key={d.id}
                {...clickableRow(() => router.push(`/lab-knowledge/documents/${d.id}`))}
                className="border-b hover:bg-gray-50 cursor-pointer"
              >
                <td className="py-2 font-medium">
                  {d.title}
                  {d.is_archived && <span className="ml-2 text-xs text-gray-500">(archived)</span>}
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
  useDismissOnEscape(true, () => onClose());
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
        // The server fetches the URL out-of-band; poll the import job until the
        // document is created (or the import fails).
        const job = await api.post<UrlImport>(`${API_BASE}/documents/import-url`, {
          url: sourceUrl.trim(),
          title: title || null,
          description: description || null,
          tag_ids: tagIds,
        });
        const deadline = Date.now() + 90_000;
        let current = job;
        while (current.status === "pending" || current.status === "running") {
          if (Date.now() > deadline) {
            setErr("Import is taking longer than expected; it will appear once it finishes.");
            setBusy(false);
            return;
          }
          await new Promise((r) => setTimeout(r, 800));
          current = await api.get<UrlImport>(`${API_BASE}/documents/url-imports/${job.id}`);
        }
        if (current.status === "failed") {
          setErr(current.error_message || "Import failed.");
          setBusy(false);
          return;
        }
        onUploaded();
        return;
      }
      const file = fileInput.current?.files?.[0];
      if (!file) {
        setErr("Choose a file to upload.");
        setBusy(false);
        return;
      }
      const uploadToken = await uploadDocumentFile(file);
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
        mode === value ? "bg-bioaf-600 text-white" : "bg-white text-gray-700 hover:bg-gray-50"
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
                    ? "bg-bioaf-600 text-white border-bioaf-600"
                    : "bg-white text-gray-700 border-gray-300"
                }`}
              >
                {t.name}
              </button>
            ))}
          </div>
          {!canManageTags && tags.length === 0 && (
            <p className="text-xs text-gray-500">No tags available.</p>
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
            className="bg-bioaf-600 text-white text-sm rounded px-4 py-1.5 disabled:opacity-50"
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
