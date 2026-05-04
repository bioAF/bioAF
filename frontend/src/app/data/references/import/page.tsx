"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { api } from "@/lib/api";
import type {
  ReferenceImportRequest,
  ReferenceImportStartResponse,
  ReferenceImportStatusResponse,
} from "@/lib/types";

const CATEGORIES = ["genome", "annotation", "index", "atlas", "markers", "other"];
const SCOPES = ["public", "internal"];
const EXTRACT_MODES: ReferenceImportRequest["extract"][] = ["none", "gzip", "tar", "tar.gz"];

const TERMINAL_STATUSES = new Set(["active", "failed"]);
const POLL_INTERVAL_MS = 5000;

function formatBytes(bytes: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export default function ImportReferencePage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [version, setVersion] = useState("");
  const [category, setCategory] = useState("annotation");
  const [scope, setScope] = useState("internal");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceMd5Url, setSourceMd5Url] = useState("");
  const [extract, setExtract] = useState<ReferenceImportRequest["extract"]>("gzip");
  const [description, setDescription] = useState("");

  const [referenceId, setReferenceId] = useState<number | null>(null);
  const [status, setStatus] = useState<ReferenceImportStatusResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollHandle = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollHandle.current) clearInterval(pollHandle.current);
    };
  }, []);

  const startPolling = (id: number) => {
    if (pollHandle.current) clearInterval(pollHandle.current);
    const tick = async () => {
      try {
        const s = await api.get<ReferenceImportStatusResponse>(
          `/api/references/${id}/import-status`,
        );
        setStatus(s);
        if (TERMINAL_STATUSES.has(s.status)) {
          if (pollHandle.current) clearInterval(pollHandle.current);
          if (s.status === "active") {
            router.push(`/data/references/${id}`);
          }
        }
      } catch {
        // keep polling on transient errors
      }
    };
    void tick();
    pollHandle.current = setInterval(tick, POLL_INTERVAL_MS);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!name || !version || !category || !scope || !sourceUrl) {
      setError("Fill in all required fields.");
      return;
    }
    setSubmitting(true);
    try {
      const init = await api.post<ReferenceImportStartResponse>(
        "/api/references/import",
        {
          name,
          version,
          category,
          scope,
          source_url: sourceUrl,
          source_md5_url: sourceMd5Url || undefined,
          extract,
          description: description || undefined,
        } satisfies ReferenceImportRequest,
      );
      setReferenceId(init.reference_id);
      setStatus({
        reference_id: init.reference_id,
        status: init.status,
        progress_pct: null,
        bytes_downloaded: null,
        total_bytes: null,
        error_message: null,
        import_job_id: init.import_job_id,
        updated_at: null,
      });
      startPolling(init.reference_id);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Import failed";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancel = async () => {
    if (referenceId == null) return;
    try {
      await api.post(`/api/references/${referenceId}/import-cancel`);
    } catch {
      // best-effort cancel
    }
    if (pollHandle.current) clearInterval(pollHandle.current);
    router.push("/data/references");
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-4">Import Reference from URL</h1>
          <p className="text-sm text-gray-600 mb-6">
            Pull a reference (FASTA, GTF, prebuilt index, ...) from a public URL via a
            per-import job that runs to completion in the background. Large transfers
            don&apos;t block this browser session.
          </p>

          {!referenceId && (
            <form onSubmit={handleSubmit} className="bg-white rounded-lg shadow p-6 space-y-4 max-w-3xl">
              <div className="grid grid-cols-2 gap-4">
                <label className="block">
                  <span className="text-sm font-medium text-gray-700">Name *</span>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
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
                    className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
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
                <span className="text-sm font-medium text-gray-700">Source URL *</span>
                <input
                  type="url"
                  value={sourceUrl}
                  onChange={(e) => setSourceUrl(e.target.value)}
                  placeholder="https://ftp.ebi.ac.uk/.../gencode.v45.annotation.gtf.gz"
                  className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                  required
                />
              </label>

              <label className="block">
                <span className="text-sm font-medium text-gray-700">Source MD5 URL (optional)</span>
                <input
                  type="url"
                  value={sourceMd5Url}
                  onChange={(e) => setSourceMd5Url(e.target.value)}
                  placeholder="https://ftp.example/MD5SUMS"
                  className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                />
              </label>

              <label className="block">
                <span className="text-sm font-medium text-gray-700">Extract</span>
                <select
                  value={extract}
                  onChange={(e) => setExtract(e.target.value as ReferenceImportRequest["extract"])}
                  className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
                >
                  {EXTRACT_MODES.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
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
                  {submitting ? "Starting..." : "Start import"}
                </button>
              </div>
            </form>
          )}

          {referenceId && status && (
            <div className="bg-white rounded-lg shadow p-6 space-y-3 max-w-3xl">
              <h2 className="text-lg font-semibold">Import in progress</h2>
              <div className="text-sm">
                Status: <span className="font-mono">{status.status}</span>
                {status.progress_pct != null && <> — {status.progress_pct}%</>}
              </div>
              {status.total_bytes != null && (
                <div className="text-sm text-gray-600">
                  {formatBytes(status.bytes_downloaded)} / {formatBytes(status.total_bytes)}
                </div>
              )}
              <div className="bg-gray-200 h-2 rounded overflow-hidden">
                <div
                  className="bg-bioaf-600 h-2 transition-all"
                  style={{ width: `${status.progress_pct ?? 0}%` }}
                />
              </div>
              {status.error_message && (
                <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2">
                  {status.error_message}
                </div>
              )}
              <div className="flex justify-end pt-2">
                <button
                  type="button"
                  onClick={handleCancel}
                  className="px-4 py-2 border border-red-300 text-red-700 rounded-md text-sm hover:bg-red-50"
                >
                  Cancel import
                </button>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
