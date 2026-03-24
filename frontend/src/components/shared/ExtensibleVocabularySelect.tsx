"use client";

import { useState } from "react";
import { useVocabulary } from "@/hooks/useVocabulary";
import { getCurrentUser } from "@/lib/auth";
import { api } from "@/lib/api";
import type { ControlledVocabularyValue } from "@/lib/types";

interface ExtensibleVocabularySelectProps {
  fieldName: string;
  value: string | null | undefined;
  onChange: (value: string | null) => void;
  placeholder?: string;
  className?: string;
  allowEmpty?: boolean;
}

const EXTENSIBLE_ROLES = ["admin", "comp_bio"];

export function ExtensibleVocabularySelect({
  fieldName,
  value,
  onChange,
  placeholder,
  className = "border rounded px-3 py-2 text-sm",
  allowEmpty = true,
}: ExtensibleVocabularySelectProps) {
  const { values, loading, addValue } = useExtensibleVocabulary(fieldName);
  const [adding, setAdding] = useState(false);
  const [newValue, setNewValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const user = getCurrentUser();
  const canExtend = user && EXTENSIBLE_ROLES.includes(user.role_name as string);

  async function handleAdd() {
    const trimmed = newValue.trim();
    if (!trimmed) return;
    setSaving(true);
    setError("");
    try {
      const created = await api.post<{ allowed_value: string }>("/api/vocabularies", {
        field_name: fieldName,
        allowed_value: trimmed,
      });
      addValue({
        id: 0,
        value: created.allowed_value,
        display_label: null,
        display_order: 0,
        is_default: false,
        is_active: true,
      });
      onChange(created.allowed_value);
      setNewValue("");
      setAdding(false);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to add value");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <select disabled className={className}>
        <option>Loading...</option>
      </select>
    );
  }

  return (
    <div>
      <select
        value={value ?? ""}
        onChange={(e) => {
          onChange(e.target.value || null);
        }}
        className={className}
      >
        {allowEmpty && (
          <option value="">
            {placeholder || `Select ${fieldName.replace(/_/g, " ")}...`}
          </option>
        )}
        {values.map((v) => (
          <option key={v.id || v.value} value={v.value}>
            {v.display_label || v.value}
          </option>
        ))}
      </select>
      {canExtend && !adding && (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="text-xs text-bioaf-600 hover:underline mt-1 block"
        >
          + Add new {fieldName.replace(/_/g, " ")}
        </button>
      )}
      {adding && (
        <div className="flex items-center gap-2 mt-2">
          <input
            type="text"
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
            placeholder={`New ${fieldName.replace(/_/g, " ")}`}
            className="border border-gray-300 rounded px-2 py-1 text-sm flex-1"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleAdd();
              }
            }}
          />
          <button
            type="button"
            onClick={handleAdd}
            disabled={saving || !newValue.trim()}
            className="text-sm bg-bioaf-600 text-white px-3 py-1 rounded hover:bg-bioaf-700 disabled:opacity-50"
          >
            {saving ? "..." : "Add"}
          </button>
          <button
            type="button"
            onClick={() => {
              setAdding(false);
              setNewValue("");
              setError("");
            }}
            className="text-sm text-gray-500 hover:text-gray-700"
          >
            Cancel
          </button>
        </div>
      )}
      {error && <p className="text-xs text-red-600 mt-1">{error}</p>}
    </div>
  );
}

function useExtensibleVocabulary(fieldName: string) {
  const vocab = useVocabulary(fieldName);
  const [extraValues, setExtraValues] = useState<ControlledVocabularyValue[]>([]);

  return {
    values: [...vocab.values, ...extraValues],
    loading: vocab.loading,
    addValue: (v: ControlledVocabularyValue) => setExtraValues((prev) => [...prev, v]),
  };
}
