"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { ReferenceDataset, ReferenceDatasetListResponse } from "@/lib/types";

/**
 * Searchable dropdown of active reference datasets, scoped to a category
 * (or 'any'). Stores the dataset's *path* (gcs_prefix mounted under
 * /data/references/) so the existing pipeline_run_service auto-linker
 * picks it up at run time without further wiring.
 */
export function ReferencePicker({
  category,
  value,
  onChange,
}: {
  category: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const [refs, setRefs] = useState<ReferenceDataset[]>([]);
  const [loading, setLoading] = useState(false);
  const [includeDeprecated, setIncludeDeprecated] = useState(false);

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams();
    if (category && category !== "any") params.set("category", category);
    api
      .get<ReferenceDatasetListResponse>(
        `/api/references${params.toString() ? `?${params}` : ""}`,
      )
      .then((data) => setRefs(data.references))
      .catch(() => setRefs([]))
      .finally(() => setLoading(false));
  }, [category]);

  const visible = useMemo(
    () =>
      refs.filter((r) =>
        includeDeprecated ? true : r.status === "active" || r.status === "pending_approval",
      ),
    [refs, includeDeprecated],
  );

  const pathFor = (r: ReferenceDataset) => `/data/references/${r.gcs_prefix}`;

  return (
    <div className="space-y-1">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full border rounded px-3 py-2 text-sm font-mono bg-white"
        disabled={loading}
      >
        <option value="">— Select a reference —</option>
        {visible.map((r) => (
          <option
            key={r.id}
            value={pathFor(r)}
            disabled={r.status === "deprecated"}
          >
            {r.name} ({r.version})
            {r.status !== "active" ? ` — ${r.status}` : ""}
          </option>
        ))}
      </select>
      <label className="inline-flex items-center gap-1.5 text-xs text-gray-500">
        <input
          type="checkbox"
          checked={includeDeprecated}
          onChange={(e) => setIncludeDeprecated(e.target.checked)}
          className="rounded"
        />
        Include deprecated versions
      </label>
      {loading && <p className="text-xs text-gray-400">Loading references...</p>}
      {!loading && visible.length === 0 && (
        <p className="text-xs text-gray-500">
          No active references in category &ldquo;{category}&rdquo;.
        </p>
      )}
    </div>
  );
}
