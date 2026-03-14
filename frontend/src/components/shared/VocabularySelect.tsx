"use client";

import { useVocabulary } from "@/hooks/useVocabulary";

interface VocabularySelectProps {
  fieldName: string;
  value: string | null | undefined;
  onChange: (value: string | null) => void;
  placeholder?: string;
  className?: string;
  allowEmpty?: boolean;
}

export function VocabularySelect({
  fieldName,
  value,
  onChange,
  placeholder,
  className = "border rounded px-3 py-2 text-sm",
  allowEmpty = true,
}: VocabularySelectProps) {
  const { values, loading } = useVocabulary(fieldName);

  if (loading) {
    return (
      <select disabled className={className}>
        <option>Loading...</option>
      </select>
    );
  }

  return (
    <select
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
