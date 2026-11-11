"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { uploadFileResumable } from "@/lib/resumableUpload";
import type {
  ReferenceDatasetListResponse,
  ReferenceUploadInitRequest,
  ReferenceUploadInitResponse,
} from "@/lib/types";
import { predictNextVersion } from "./referenceVersioning";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { Input, Select, Textarea } from "@/components/ui/Input";
import { Alert } from "@/components/shared/Alert";
import { formatBytes } from "@/lib/bytes";
import { NOT_SET } from "@/lib/placeholders";

const CATEGORIES = ["genome", "annotation", "index", "atlas", "markers", "other"];
const SCOPES = ["public", "internal"];

interface FileProgress {
  filename: string;
  size: number;
  uploaded: number;
  status: "pending" | "uploading" | "done" | "error";
  error?: string;
}

interface UploadReferenceFormProps {
  lockedName?: string;
  lockedCategory?: string;
  lockedScope?: string;
  onCreated: (referenceId: number) => void;
  onCancel: () => void;
}

export function UploadReferenceForm({
  lockedName,
  lockedCategory,
  lockedScope,
  onCreated,
  onCancel,
}: UploadReferenceFormProps) {
  const [name, setName] = useState(lockedName ?? "");
  const [version, setVersion] = useState("");
  const [category, setCategory] = useState(lockedCategory || "genome");
  const [scope, setScope] = useState(lockedScope || "internal");
  const [sourceUrl, setSourceUrl] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [progress, setProgress] = useState<FileProgress[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Track the last value the auto-prediction wrote, so we can overwrite our
  // own prediction when name/category changes but never clobber a value the
  // user typed manually.
  const lastPredictedRef = useRef<string>("");

  const refreshPredictedVersion = async (n: string, c: string) => {
    if (!n || !c) return;
    try {
      const params = new URLSearchParams({ name: n, category: c });
      const data = await api.get<ReferenceDatasetListResponse>(
        `/api/references/by-name?${params.toString()}`,
      );
      const next = predictNextVersion(data.references.map((r) => r.version));
      setVersion((current) => {
        if (current === "" || current === lastPredictedRef.current) {
          lastPredictedRef.current = next;
          return next;
        }
        return current;
      });
    } catch {
      // best-effort; user can still type a version.
    }
  };

  useEffect(() => {
    if (lockedName) void refreshPredictedVersion(lockedName, lockedCategory || category);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lockedName, lockedCategory]);

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
    setProgress((prev) => prev.map((p) => (p.filename === filename ? { ...p, ...patch } : p)));
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
        if (!slot) throw new Error(`Server did not return a session URL for ${file.name}`);
        updateProgress(file.name, { status: "uploading" });
        await uploadFileResumable(slot.session_url, file, {
          onProgress: (uploaded) => updateProgress(file.name, { uploaded }),
        });
        updateProgress(file.name, { status: "done", uploaded: file.size });
      }

      await api.post(`/api/references/${referenceId}/upload-complete`);
      onCreated(referenceId);
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
    <form onSubmit={handleSubmit} className="bg-white rounded-lg shadow p-6 space-y-4 max-w-3xl">
      <div className="grid grid-cols-2 gap-4">
        <Field label="Name *">
          {(control) => (
            <Input
              {...control}
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onBlur={() => void refreshPredictedVersion(name, category)}
              readOnly={Boolean(lockedName)}
              required
            />
          )}
        </Field>
        <Field label="Version" hint="Filled in automatically; override it to keep your own scheme.">
          {(control) => (
            <Input
              {...control}
              type="text"
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              placeholder="v1"
            />
          )}
        </Field>
        <Field label="Category *">
          {(control) => (
            <Select
              {...control}
              value={category}
              onChange={(e) => {
                setCategory(e.target.value);
                if (name) void refreshPredictedVersion(name, e.target.value);
              }}
              disabled={Boolean(lockedCategory)}
              required
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Scope *">
          {(control) => (
            <Select {...control} value={scope} onChange={(e) => setScope(e.target.value)} required>
              {SCOPES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>

      <Field label="Source URL (optional)">
        {(control) => (
          <Input
            {...control}
            type="url"
            value={sourceUrl}
            onChange={(e) => setSourceUrl(e.target.value)}
            placeholder="https://www.gencodegenes.org/..."
          />
        )}
      </Field>

      <Field label="Description (optional)">
        {(control) => (
          <Textarea {...control} value={description} onChange={(e) => setDescription(e.target.value)} rows={2} />
        )}
      </Field>

      <Field label="Files *">
        {(control) => (
          <input
            {...control}
            type="file"
            multiple
            onChange={handleFiles}
            disabled={submitting}
            className="block w-full text-sm"
          />
        )}
      </Field>

      {progress.length > 0 && (
        <div className="border-t pt-4 space-y-2">
          {progress.map((p) => (
            <div key={p.filename} className="text-sm">
              <div className="flex justify-between">
                <span className="font-mono">{p.filename}</span>
                <span className="text-gray-500">
                  {formatBytes(p.uploaded, "reference", { missing: NOT_SET })} / {formatBytes(p.size, "reference", { missing: NOT_SET })},{" "}
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

      {error && <Alert tone="error">{error}</Alert>}

      <div className="flex justify-end gap-3 pt-2">
        <Button variant="secondary" onClick={onCancel} disabled={submitting}>
          Cancel
        </Button>
        <Button type="submit" busy={submitting} busyLabel="Uploading...">
          Start upload
        </Button>
      </div>
    </form>
  );
}
