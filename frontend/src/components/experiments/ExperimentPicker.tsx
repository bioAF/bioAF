"use client";

import { useEffect, useState } from "react";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useResource } from "@/hooks/useResource";
import {
  EXPERIMENT_PAGE_SIZE,
  experimentById,
  experimentPage,
  type ExperimentOption,
} from "@/lib/experimentOptions";
import { isPermissionDenied } from "@/lib/projectOptions";

/**
 * Choose one experiment from all of the Organization's, a page of 100 at a time (issues #104, #105).
 *
 * Search matches names on the server, and the project filter is applied there too, so every eligible
 * experiment is reachable however many there are. A selection that is not on the page shown keeps its
 * name (read by id). Reads belong to the session and the current query (useResource), so nothing from an
 * earlier session or query is shown. Loading, empty, no access and failure are each said plainly.
 */
export function ExperimentPicker({
  id,
  label,
  value,
  onChange,
  projectId = null,
  noneLabel = "No experiment",
  disabled = false,
  variant = "form",
  className = "border rounded px-3 py-2 text-sm w-full",
  optionLabel = (exp) => `${exp.name}${exp.code ? ` (${exp.code})` : ""}`,
}: {
  /** The select's id, for a visible <label htmlFor>; ``label`` names it for assistive technology. */
  id?: string;
  label: string;
  value: number | null;
  onChange: (id: number | null, experiment: ExperimentOption | null) => void;
  projectId?: number | null;
  noneLabel?: string;
  disabled?: boolean;
  /** Filter rows align the select with adjacent controls; search and paging sit below it. */
  variant?: "form" | "filter";
  className?: string;
  optionLabel?: (experiment: ExperimentOption) => string;
}) {
  const [search, setSearch] = useState("");
  const name = useDebouncedValue(search.trim(), 300);
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [name, projectId]);

  const list = useResource(`experiments|${name}|${projectId ?? ""}|${page}`, (signal) =>
    experimentPage({ name, projectId, page }, signal), { expected: isPermissionDenied });
  const rows = list.data?.experiments ?? [];
  const total = list.data?.total ?? 0;
  const onPage = value != null && rows.some((e) => e.id === value);
  const selected = useResource(value != null && !onPage ? `experiment|${value}` : null, (signal) =>
    experimentById(value as number, signal));

  const options = [...rows];
  if (value != null && !onPage) {
    options.unshift(selected.data ?? { id: value, name: `Experiment ${value}` });
  }
  const first = total === 0 ? 0 : (page - 1) * EXPERIMENT_PAGE_SIZE + 1;
  const last = Math.min(page * EXPERIMENT_PAGE_SIZE, total);

  const searchInput = (
    <input
      type="search"
      aria-label="Search names"
      placeholder="Search by name"
      value={search}
      onChange={(e) => setSearch(e.target.value)}
      disabled={disabled}
      className="border rounded px-3 py-1.5 text-sm w-full"
    />
  );
  const experimentSelect = (
    <select
      id={id}
      aria-label={label}
      value={value ?? ""}
      disabled={disabled || list.denied}
      onChange={(e) => {
        const id = e.target.value ? Number(e.target.value) : null;
        onChange(id, id == null ? null : (options.find((o) => o.id === id) ?? null));
      }}
      className={`${className} min-w-0 max-w-full`}
    >
      <option value="">{noneLabel}</option>
      {options.map((exp) => (
        <option key={exp.id} value={exp.id}>
          {optionLabel(exp)}
        </option>
      ))}
    </select>
  );

  return (
    <div className="min-w-0 space-y-1.5">
      {variant === "filter" ? (
        <>{experimentSelect}{searchInput}</>
      ) : (
        <>{searchInput}{experimentSelect}</>
      )}
      <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1 text-xs text-gray-500">
        {list.denied ? (
          <span>You do not have access to experiments.</span>
        ) : list.error && !list.data ? (
          <span>
            Experiments could not be loaded.{" "}
            <button type="button" onClick={() => void list.refresh()} className="underline">
              Retry
            </button>
          </span>
        ) : list.loading ? (
          <span>Loading experiments…</span>
        ) : total === 0 ? (
          <span>No experiments match.</span>
        ) : (
          <span>
            {first}–{last} of {total}
          </span>
        )}
        <span className="flex gap-2">
          <button
            type="button"
            aria-label="Previous page"
            disabled={page <= 1 || list.loading}
            onClick={() => setPage((p) => p - 1)}
            className="disabled:opacity-40"
          >
            Previous
          </button>
          <button
            type="button"
            aria-label="Next page"
            disabled={last >= total || list.loading}
            onClick={() => setPage((p) => p + 1)}
            className="disabled:opacity-40"
          >
            Next
          </button>
        </span>
      </div>
    </div>
  );
}
