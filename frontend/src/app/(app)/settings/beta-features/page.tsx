"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";

// Labels for the known beta features. The backend registry (beta_features_service.BETA_FEATURES) is the
// source of truth for WHICH flags exist (returned in `flags`); this map supplies their display copy.
const BETA_FEATURE_LABELS: Record<string, { label: string; description: string }> = {
  lit_validation: {
    label: "Literature Validation",
    description: "AI-assisted reproduction triage for scientific papers.",
  },
};

interface BetaState {
  flags: Record<string, boolean>;
}

export default function BetaFeaturesPage() {
  const [state, setState] = useState<BetaState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .get<BetaState>("/api/beta-features")
      .then(setState)
      .catch(() => setError("Failed to load beta features."));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = async (key: string, enabled: boolean) => {
    setSaving(key);
    setError(null);
    try {
      const next = await api.put<BetaState>(`/api/beta-features/${key}`, { enabled });
      setState(next);
    } catch {
      setError("Failed to update the flag. You may not have permission on this instance.");
    } finally {
      setSaving(null);
    }
  };

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <h1 className="text-2xl font-bold mb-2">Beta Features</h1>
      <p data-testid="page-description" className="text-sm text-gray-500 mb-6">
        Preview features that are not yet enabled for everyone. Toggles here affect this entire
        instance.
      </p>

      {error && <p className="mb-4 text-sm text-red-600">{error}</p>}
      {state && (
        <div className="max-w-2xl space-y-3">
          {Object.entries(state.flags).map(([key, enabled]) => {
            const meta = BETA_FEATURE_LABELS[key] ?? { label: key, description: "" };
            const disabled = saving === key;
            return (
              <div
                key={key}
                className="flex items-center justify-between rounded-lg border border-gray-200 p-4"
              >
                <div>
                  <div className="font-medium text-gray-900">{meta.label}</div>
                  {meta.description && <div className="text-sm text-gray-500">{meta.description}</div>}
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={enabled}
                  aria-label={`Toggle ${meta.label}`}
                  disabled={disabled}
                  onClick={() => toggle(key, !enabled)}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                    enabled ? "bg-bioaf-600" : "bg-gray-300"
                  } ${disabled ? "cursor-not-allowed opacity-50" : ""}`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                      enabled ? "translate-x-6" : "translate-x-1"
                    }`}
                  />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </main>
  );
}
