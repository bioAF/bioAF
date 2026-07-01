"use client";

import { SAMPLE_ASSAY_OPTIONS } from "@/lib/types";

interface AssaySelectProps {
  value: string | null | undefined;
  onChange: (value: string | null) => void;
  placeholder?: string;
  className?: string;
  id?: string;
}

// A dropdown over the fixed, system-wide assay vocabulary (SAMPLE_ASSAYS). Unlike
// VocabularySelect, which fetches per-org configurable vocab, the assay vocabulary is a
// fixed system enum, so the options are static and need no hook. An empty selection means
// "no explicit assay" (the backend then infers it from the free-text fields).
export function AssaySelect({
  value,
  onChange,
  placeholder = "Assay...",
  className = "border rounded px-3 py-2 text-sm",
  id,
}: AssaySelectProps) {
  return (
    <select
      id={id}
      aria-label="Assay"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
      className={className}
    >
      <option value="">{placeholder}</option>
      {SAMPLE_ASSAY_OPTIONS.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}
