"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  ReferenceDatasetListResponse,
  ReferenceImportRequest,
  ReferenceImportStartResponse,
} from "@/lib/types";
import { extractModeForUrl, predictNextVersion } from "./referenceVersioning";
import { Button } from "@/components/ui/Button";

const CATEGORIES = ["genome", "annotation", "index", "atlas", "markers", "other"];
const SCOPES = ["public", "internal"];
const EXTRACT_MODES: ReferenceImportRequest["extract"][] = ["none", "gzip", "tar", "tar.gz"];

interface UrlImportReferenceFormProps {
  lockedName?: string;
  lockedCategory?: string;
  lockedScope?: string;
  onStarted: (referenceId: number) => void;
  onCancel: () => void;
}

export function UrlImportReferenceForm({
  lockedName,
  lockedCategory,
  lockedScope,
  onStarted,
  onCancel,
}: UrlImportReferenceFormProps) {
  const [name, setName] = useState(lockedName ?? "");
  const [version, setVersion] = useState("");
  const [category, setCategory] = useState(lockedCategory || "annotation");
  const [scope, setScope] = useState(lockedScope || "internal");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceMd5Url, setSourceMd5Url] = useState("");
  const [extract, setExtract] = useState<ReferenceImportRequest["extract"]>("none");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastPredictedVersionRef = useRef<string>("");
  // Auto-detection ownership: stays true until the user changes the extract
  // dropdown themselves. Once flipped, URL changes never overwrite their
  // choice (even if the user picks back to 'none' that matches what the
  // auto-detector would have predicted).
  const extractAutoOwnedRef = useRef<boolean>(true);

  const refreshPredictedVersion = async (n: string, c: string) => {
    if (!n || !c) return;
    try {
      const params = new URLSearchParams({ name: n, category: c });
      const data = await api.get<ReferenceDatasetListResponse>(
        `/api/references/by-name?${params.toString()}`,
      );
      const next = predictNextVersion(data.references.map((r) => r.version));
      setVersion((current) => {
        if (current === "" || current === lastPredictedVersionRef.current) {
          lastPredictedVersionRef.current = next;
          return next;
        }
        return current;
      });
    } catch {
      // best-effort
    }
  };

  useEffect(() => {
    if (lockedName) void refreshPredictedVersion(lockedName, lockedCategory || category);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lockedName, lockedCategory]);

  const handleUrlChange = (next: string) => {
    setSourceUrl(next);
    if (extractAutoOwnedRef.current) {
      setExtract(extractModeForUrl(next));
    }
  };

  const handleExtractChange = (next: ReferenceImportRequest["extract"]) => {
    setExtract(next);
    extractAutoOwnedRef.current = false;
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
      const init = await api.post<ReferenceImportStartResponse>("/api/references/import", {
        name,
        version,
        category,
        scope,
        source_url: sourceUrl,
        source_md5_url: sourceMd5Url || undefined,
        extract,
        description: description || undefined,
      } satisfies ReferenceImportRequest);
      onStarted(init.reference_id);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Import failed";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="bg-white rounded-lg shadow p-6 space-y-4 max-w-3xl">
      <div className="grid grid-cols-2 gap-4">
        <label className="block">
          <span className="text-sm font-medium text-gray-700">Name *</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => void refreshPredictedVersion(name, category)}
            readOnly={Boolean(lockedName)}
            className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm read-only:bg-gray-50 read-only:text-gray-600"
            required
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-gray-700">
            Version
            <span className="ml-2 text-xs font-normal text-gray-500">
              (auto, override to keep your own scheme)
            </span>
          </span>
          <input
            type="text"
            value={version}
            onChange={(e) => setVersion(e.target.value)}
            placeholder="v1"
            className="mt-1 block w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-gray-700">Category *</span>
          <select
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              if (name) void refreshPredictedVersion(name, e.target.value);
            }}
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
        <span className="text-sm font-medium text-gray-700">Source URL *</span>
        <input
          type="url"
          value={sourceUrl}
          onChange={(e) => handleUrlChange(e.target.value)}
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
        <span className="text-sm font-medium text-gray-700">
          Extract
          <span className="ml-2 text-xs font-normal text-gray-500">(auto from URL extension)</span>
        </span>
        <select
          value={extract}
          onChange={(e) => handleExtractChange(e.target.value as ReferenceImportRequest["extract"])}
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
          onClick={onCancel}
          disabled={submitting}
          className="px-4 py-2 border border-gray-300 rounded-md text-sm hover:bg-gray-50"
        >
          Cancel
        </button>
        <Button type="submit"
          disabled={submitting}>
          {submitting ? "Starting..." : "Start import"}
        </Button>
      </div>
    </form>
  );
}
