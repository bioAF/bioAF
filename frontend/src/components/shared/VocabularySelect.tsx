"use client";

import type { AriaAttributes } from "react";
import { useVocabulary } from "@/hooks/useVocabulary";

interface VocabularySelectProps {
  fieldName: string;
  value: string | null | undefined;
  onChange: (value: string | null) => void;
  placeholder?: string;
  className?: string;
  allowEmpty?: boolean;
  /** From a Field: a visible label then names the select instead of aria-label. */
  id?: string;
  "aria-describedby"?: string;
  /** From a Field with an error. */
  "aria-invalid"?: AriaAttributes["aria-invalid"];
}

export function VocabularySelect({
  fieldName,
  value,
  onChange,
  placeholder,
  className = "border rounded px-3 py-2 text-sm",
  allowEmpty = true,
  id,
  "aria-describedby": describedBy,
  "aria-invalid": invalid,
}: VocabularySelectProps) {
  const { values, loading } = useVocabulary(fieldName);
  // With an id a visible label names the select, and aria-label would override it.
  const ariaLabel = id ? undefined : placeholder ?? fieldName;

  if (loading) {
    return (
      <select disabled id={id} aria-describedby={describedBy} aria-invalid={invalid} aria-label={ariaLabel} className={className}>
        <option>Loading...</option>
      </select>
    );
  }

  return (
    <select
      id={id}
      aria-describedby={describedBy}
      aria-invalid={invalid}
      aria-label={ariaLabel}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
      className={className}
    >
      {allowEmpty && <option value="">{placeholder || `Select ${fieldName.replace(/_/g, " ")}...`}</option>}
      {values.map((v) => (
        <option key={v.id} value={v.value}>
          {v.display_label || v.value}
        </option>
      ))}
    </select>
  );
}
