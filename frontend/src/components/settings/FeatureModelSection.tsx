"use client";

/**
 * plan_6 steps 6 and 7: which model each literature feature runs on, and whether it can do the job.
 *
 * Validation reads a whole paper against a 23-metric vocabulary; review scores relevance over short
 * abstracts. One model for both is a compromise, and it used to be an invisible one.
 *
 * The suitability banner states the REASON rather than a verdict, because a reason is something a
 * user can check against their own papers. It never blocks a save: the table behind it is curated
 * judgment with no measurement under it, and bioAF does not get to overrule a lab about its own
 * model on that basis.
 */

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";

const PROVIDERS = ["openai", "anthropic", "google", "gemma"];

const LABELS: Record<string, string> = {
  literature_validation: "Literature Validation",
  literature_review: "Literature Review",
};

interface Suitability {
  verdict: string;
  reason: string;
  warn: boolean;
  note: string;
  blocks_save: boolean;
}

interface FeatureModel {
  feature: string;
  provider: string | null;
  model: string | null;
  overridden: boolean;
  suitability: Suitability;
}

interface ProviderModelList {
  provider: string;
  models: string[];
  used_fallback: boolean;
}

export function FeatureModelSection() {
  const [features, setFeatures] = useState<FeatureModel[] | null>(null);
  // The same per-provider model lists the provider cards use, so this picker offers exactly what
  // that one does. A free-text model id asks the user to recall it from memory and typo it silently.
  const [modelLists, setModelLists] = useState<ProviderModelList[]>([]);
  const [draft, setDraft] = useState<Record<string, { provider: string; model: string }>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [r, providers] = await Promise.all([
        api.get<{ features: FeatureModel[] }>("/api/integrations/llm/feature-models"),
        api.get<{ model_lists: ProviderModelList[] }>("/api/integrations/llm/providers"),
      ]);
      setModelLists(providers?.model_lists ?? []);
      setFeatures(r.features);
      setDraft(
        Object.fromEntries(
          r.features.map((f) => [
            f.feature,
            { provider: f.provider ?? "anthropic", model: f.model ?? "" },
          ]),
        ),
      );
    } catch (e) {
      logError("loading the per-feature LLM models", e);
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(feature: string) {
    setError(null);
    setBusy(feature);
    try {
      await api.put(`/api/integrations/llm/feature-models/${feature}`, {
        provider: draft[feature].provider,
        model: draft[feature].model,
      });
      await load();
    } catch (e) {
      logError("saving a per-feature LLM model", e);
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function clear(feature: string) {
    setError(null);
    setBusy(feature);
    try {
      await api.delete(`/api/integrations/llm/feature-models/${feature}`);
      await load();
    } catch (e) {
      logError("clearing a per-feature LLM model", e);
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  /** The provider's models, with the saved one kept even when the provider no longer lists it.
   *
   * A model can be retired between saving an override and opening this page. Dropping it here would
   * silently swap what the feature runs on, so it stays selectable until the user changes it. */
  function modelsFor(provider: string | undefined, current: string | undefined): string[] {
    const listed = modelLists.find((m) => m.provider === provider)?.models ?? [];
    return current && !listed.includes(current) ? [current, ...listed] : listed;
  }

  const usedFallback = modelLists.some((m) => m.used_fallback);

  if (features === null && !error) return null;

  return (
    <div className="bg-white border border-gray-200 rounded p-4 space-y-3">
      <h3 className="font-semibold text-sm">Models Per Literature Feature</h3>
      <p className="text-xs text-gray-500">
        Reading a whole paper is a different job from scoring an abstract. Leave a feature on the
        org default, or give it its own model on a provider you have already configured.
      </p>
      {usedFallback && (
        <p className="text-xs text-amber-700">
          Model list shown from local fallback; live fetch failed.
        </p>
      )}
      {error && <p className="text-xs text-red-600">{error}</p>}
      {(features ?? []).map((f) => (
        <div key={f.feature} className="border-t border-gray-100 pt-3 space-y-2">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-sm font-medium">{LABELS[f.feature] ?? f.feature}</span>
            <span className="font-mono text-xs text-gray-600">
              {f.provider ?? "none"} / {f.model ?? "none"}
            </span>
            <span className="text-xs text-gray-500">
              {f.overridden ? "own model" : "org default"}
            </span>
          </div>

          {f.suitability.warn ? (
            <p
              role="alert"
              className="rounded border border-amber-200 bg-amber-50 px-2 py-1.5 text-xs text-amber-800"
            >
              {f.suitability.reason} {f.suitability.note}
            </p>
          ) : (
            <p className="text-xs text-gray-500">
              {f.suitability.reason} {f.suitability.note}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <label className="sr-only" htmlFor={`${f.feature}-provider`}>
              {`${LABELS[f.feature] ?? f.feature} provider`}
            </label>
            <select
              id={`${f.feature}-provider`}
              value={draft[f.feature]?.provider ?? "anthropic"}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  [f.feature]: { ...d[f.feature], provider: e.target.value },
                }))
              }
              className="border border-gray-300 rounded px-3 py-1.5 text-sm"
            >
              {PROVIDERS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            <label className="sr-only" htmlFor={`${f.feature}-model`}>
              {`${LABELS[f.feature] ?? f.feature} model`}
            </label>
            <select
              id={`${f.feature}-model`}
              value={draft[f.feature]?.model ?? ""}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  [f.feature]: { ...d[f.feature], model: e.target.value },
                }))
              }
              className="border border-gray-300 rounded px-3 py-1.5 text-sm w-56"
            >
              <option value="">Select a model</option>
              {modelsFor(draft[f.feature]?.provider, draft[f.feature]?.model).map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <Button size="sm" onClick={() => save(f.feature)} disabled={busy === f.feature}>
              Save
            </Button>
            {f.overridden && (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => clear(f.feature)}
                disabled={busy === f.feature}
              >
                Use org default
              </Button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
