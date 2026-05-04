"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { api } from "@/lib/api";
import { uploadFileResumable } from "@/lib/resumableUpload";
import type {
  ReferenceUploadInitRequest,
  ReferenceUploadInitResponse,
} from "@/lib/types";

const CATEGORIES = ["genome", "annotation", "index", "atlas", "markers", "other"];
const SCOPES = ["public", "internal"];

interface FileProgress {
  filename: string;
  size: number;
  uploaded: number;
  status: "pending" | "uploading" | "done" | "error";
  error?: string;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export default function NewReferencePage() {
  // useSearchParams forces client-side rendering and must be wrapped in a
  // Suspense boundary for Next.js's prerender pass to skip this page cleanly.
  return (
    <Suspense fallback={null}>
      <NewReferenceForm />
    </Suspense>
  );
}

function NewReferenceForm() {
  const router = useRouter();
  const search = useSearchParams();
  // When invoked as 'Upload new version' from a reference detail page, the
  // caller passes name/category/scope so we lock those fields and the user
  // only fills in version + files.
  const lockedName = search?.get("name") ?? "";
  const lockedCategory = search?.get("category") ?? "";
  const lockedScope = search?.get("scope") ?? "";
  const [name, setName] = useState(lockedName);
  const [version, setVersion] = useState("");
  const [category, setCategory] = useState(lockedCategory || "genome");
  const [scope, setScope] = useState(lockedScope || "internal");
  const [sourceUrl, setSourceUrl] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [progress, setProgress] = useState<FileProgress[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFiles = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(e.target.files ?? []);
    setFiles(selected);
    setProgress(
      selected.map((f) => ({
        filename: f.name,
        size: f.size,
        uploaded: 0,
        status: "pending",
      })),
    );
  };

  const updateProgress = (filename: string, patch: Partial<FileProgress>) => {
    setProgress((prev) =>
      prev.map((p) => (p.filename === filename ? { ...p, ...patch } : p)),
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!name || !version || !category || !scope || files.length === 0) {
      setError("Fill in all required fields and select at least one file.");
      return;
    }
    setSubmitting(true);

    const initBody: ReferenceUploadInitRequest = {
      name,
      version,
      category,
      scope,
      source_url: sourceUrl || undefined,
      description: description || undefined,
      files: files.map((f) => ({
        filename: f.name,
        size_bytes: f.size,
        content_type: f.type || undefined,
      })),
    };

    let referenceId: number | null = null;
    try {
      const init = await api.post<ReferenceUploadInitResponse>(
        "/api/references/upload-init",
        initBody,
      );
      referenceId = init.reference_id;

      const slotByFilename = new Map(init.uploads.map((u) => [u.filename, u]));
      for (const file of files) {
        const slot = slotByFilename.get(file.name);
        if (!slot) {
          throw new Error(`Server did not return a session URL for ${file.name}`);
        }
        updateProgress(file.name, { status: "uploading" });
        await uploadFileResumable(slot.session_url, file, {
          onProgress: (uploaded) => updateProgress(file.name, { uploaded }),
        });
        updateProgress(file.name, { status: "done", uploaded: file.size });
      }

      await api.post(`/api/references/${referenceId}/upload-complete`);
      router.push(`/data/references/${referenceId}`);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Upload failed";
      setError(message);
      if (referenceId != null) {
        try {
          await api.post(`/api/references/${referenceId}/abort`);
        } catch {
          // best-effort cleanup
        }
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-4">Add Reference Dataset</h1>
          <p className="text-sm text-gray-600 mb-6">
            Upload reference files (genome FASTA, annotation GTF, prebuilt indices, etc.)
            directly to GCS via a resumable session — large files survive page reloads
            and flaky connections.
          </p>

          <form onSubmit={handleSubmit} className="bg-white rounded-lg shadow p-6 space-y-4 max-w-3xl">
            <div className="grid grid-cols-2 gap-4">
              <label className="block">
                <span className="text-sm font-medium text-gray-700">Name *</span>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  readOnly={Boolean(lockedName)}
                  className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm read-only:bg-gray-50 read-only:text-gray-600"
                  required
                />
              </label>
              <label className="block">
                <span className="text-sm font-medium text-gray-700">Version *</span>
                <input
                  type="text"
                  value={version}
                  onChange={(e) => setVersion(e.target.value)}
                  className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  required
                />
              </label>
              <label className="block">
                <span className="text-sm font-medium text-gray-700">Category *</span>
                <select
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  disabled={Boolean(lockedCategory)}
                  className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm disabled:bg-gray-50 disabled:text-gray-600"
                  required
                >
                  {CATEGORIES.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="text-sm font-medium text-gray-700">Scope *</span>
                <select
                  value={scope}
                  onChange={(e) => setScope(e.target.value)}
                  className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  required
                >
                  {SCOPES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <label className="block">
              <span className="text-sm font-medium text-gray-700">Source URL (optional)</span>
              <input
                type="url"
                value={sourceUrl}
                onChange={(e) => setSourceUrl(e.target.value)}
                placeholder="https://www.gencodegenes.org/..."
                className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
              />
            </label>

            <label className="block">
              <span className="text-sm font-medium text-gray-700">Description (optional)</span>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
                className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
              />
            </label>

            <label className="block">
              <span className="text-sm font-medium text-gray-700">Files *</span>
              <input
                type="file"
                multiple
                onChange={handleFiles}
                disabled={submitting}
                className="mt-1 block w-full text-sm"
              />
            </label>

            {progress.length > 0 && (
              <div className="border-t pt-4 space-y-2">
                {progress.map((p) => (
                  <div key={p.filename} className="text-sm">
                    <div className="flex justify-between">
                      <span className="font-mono">{p.filename}</span>
                      <span className="text-gray-500">
                        {formatBytes(p.uploaded)} / {formatBytes(p.size)} —{" "}
                        <span
                          className={
                            p.status === "done"
                              ? "text-green-600"
                              : p.status === "error"
                                ? "text-red-600"
                                : "text-gray-600"
                          }
                        >
                          {p.status}
                        </span>
                      </span>
                    </div>
                    <div className="bg-gray-200 h-2 rounded mt-1 overflow-hidden">
                      <div
                        className="bg-bioaf-600 h-2 transition-all"
                        style={{ width: `${p.size === 0 ? 0 : (p.uploaded / p.size) * 100}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}

            {error && (
              <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2">
                {error}
              </div>
            )}

            <div className="flex justify-end gap-3 pt-2">
              <button
                type="button"
                onClick={() => router.push("/data/references")}
                disabled={submitting}
                className="px-4 py-2 border border-gray-300 rounded-md text-sm hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting}
                className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50"
              >
                {submitting ? "Uploading..." : "Start upload"}
              </button>
            </div>
          </form>
        </main>
      </div>
    </div>
  );
}
