"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { literature } from "@/lib/literature";

type ProviderId = "openai" | "anthropic" | "google" | "gemma";

const HOSTED: ReadonlyArray<ProviderId> = ["openai", "anthropic", "google"];
// Gemma is intentionally omitted: the self-hosted orchestrator integration
// is stubbed in v1 (a Gemma-active review sits in 'pending' forever), so we
// hide the option from the Settings page until that lands. The backend
// "gemma" support remains; type and label below stay so the active-provider
// banner still renders correctly for any org that activated Gemma before
// this hide landed.
const ALL_PROVIDERS: ReadonlyArray<ProviderId> = ["openai", "anthropic", "google"];

// Providers whose clients support native tool-calling, which the action-taking AI Assistant
// requires. Mirrors the backend SUPPORTS_TOOLS capability (app/services/llm_provider_clients):
// Anthropic/OpenAI/Google yes, self-hosted Gemma no. Used to warn an admin whose active provider
// cannot power the assistant.
const TOOL_CAPABLE: ReadonlyArray<ProviderId> = ["openai", "anthropic", "google"];

const PROVIDER_LABEL: Record<ProviderId, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic Claude",
  google: "Google Gemini",
  gemma: "Gemma 4 (self-hosted)",
};

interface ProviderConfigSummary {
  provider: ProviderId;
  model: string | null;
  api_key_prefix_last5: string | null;
  is_active: boolean;
  configured: boolean;
}

interface ProviderModelList {
  provider: ProviderId;
  models: string[];
  used_fallback: boolean;
}

interface ProvidersResponse {
  configs: ProviderConfigSummary[];
  active_provider: ProviderId | null;
  model_lists: ProviderModelList[];
}

export function LlmSettingsContent() {
  const [data, setData] = useState<ProvidersResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingProvider, setSavingProvider] = useState<ProviderId | null>(null);
  const [pendingActivate, setPendingActivate] = useState<ProviderId | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.get<ProvidersResponse>(
        "/api/integrations/llm/providers",
      );
      setData(resp);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const modelsByProvider = useMemo(() => {
    const m = new Map<ProviderId, ProviderModelList>();
    for (const ml of data?.model_lists ?? []) m.set(ml.provider, ml);
    return m;
  }, [data]);

  const configsByProvider = useMemo(() => {
    const m = new Map<ProviderId, ProviderConfigSummary>();
    for (const c of data?.configs ?? []) m.set(c.provider, c);
    return m;
  }, [data]);

  async function handleSave(
    provider: ProviderId,
    apiKey: string | null,
    model: string,
  ) {
    setSavingProvider(provider);
    setError(null);
    try {
      await api.post(`/api/integrations/llm/providers/${provider}`, {
        api_key: apiKey,
        model,
      });
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSavingProvider(null);
    }
  }

  async function handleActivate(provider: ProviderId) {
    if (HOSTED.includes(provider)) {
      setPendingActivate(provider);
      return;
    }
    await doActivate(provider);
  }

  async function doActivate(provider: ProviderId) {
    setError(null);
    try {
      await api.post(`/api/integrations/llm/providers/${provider}/activate`);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPendingActivate(null);
    }
  }

  async function handleDeactivateAll() {
    setError(null);
    try {
      await api.post("/api/integrations/llm/providers/deactivate");
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function handleDelete(provider: ProviderId) {
    if (!confirm(`Remove the ${PROVIDER_LABEL[provider]} configuration?`)) return;
    setError(null);
    try {
      await api.delete(`/api/integrations/llm/providers/${provider}`);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (loading) return <div className="text-gray-500">Loading...</div>;
  if (error)
    return (
      <div className="bg-red-50 border border-red-200 rounded p-4 text-red-700">
        {error}
      </div>
    );

  return (
    <div className="space-y-6 max-w-3xl">
      <p className="text-sm text-gray-600">
        Configure the LLM provider that powers Agent Review and the AI Assistant.
        Exactly one provider is active at a time. Hosted providers transmit data
        to a third party.
      </p>

      <div
        className="bg-blue-50 border border-blue-200 rounded p-3 text-sm text-blue-900"
        data-testid="assistant-model-guidance"
      >
        The AI Assistant can take actions on a user&apos;s behalf and needs
        reliable tool-calling. Anthropic, OpenAI, and Google support this; the
        self-hosted model does not. Pick a current flagship model, since smaller
        or older models may call tools unreliably. The provider and model are set
        here by an administrator; users cannot change them.
      </div>

      {data?.active_provider && (
        <div className="flex items-center justify-between bg-bioaf-50 border border-bioaf-200 rounded p-3 text-sm">
          <div>
            Active provider:{" "}
            <strong>{PROVIDER_LABEL[data.active_provider]}</strong>
          </div>
          <button
            onClick={handleDeactivateAll}
            className="text-bioaf-700 hover:underline"
          >
            Disable LLM
          </button>
        </div>
      )}

      {data?.active_provider && !TOOL_CAPABLE.includes(data.active_provider) && (
        <div
          className="bg-amber-50 border border-amber-200 rounded p-3 text-sm text-amber-900"
          data-testid="active-not-tool-capable"
        >
          The active provider does not support the AI Assistant. Switch to
          Anthropic, OpenAI, or Google to enable it. (Agent Review still works.)
        </div>
      )}

      <LitReviewThresholdSection />

      <AutoLitReviewSection />


      <div className="space-y-4">
        {ALL_PROVIDERS.map((provider) => (
          <ProviderCard
            key={provider}
            provider={provider}
            config={configsByProvider.get(provider)}
            models={modelsByProvider.get(provider)}
            saving={savingProvider === provider}
            onSave={(key, model) => handleSave(provider, key, model)}
            onActivate={() => handleActivate(provider)}
            onDelete={() => handleDelete(provider)}
          />
        ))}
      </div>

      {pendingActivate !== null && (
        <DataEgressWarningModal
          provider={pendingActivate}
          onConfirm={() => doActivate(pendingActivate)}
          onCancel={() => setPendingActivate(null)}
        />
      )}
    </div>
  );
}

interface ProviderCardProps {
  provider: ProviderId;
  config: ProviderConfigSummary | undefined;
  models: ProviderModelList | undefined;
  saving: boolean;
  onSave: (apiKey: string | null, model: string) => void;
  onActivate: () => void;
  onDelete: () => void;
}

interface TestResult {
  ok: boolean;
  model_count: number | null;
  error: string | null;
  error_class: string | null;
}

function ProviderCard({
  provider,
  config,
  models,
  saving,
  onSave,
  onActivate,
  onDelete,
}: ProviderCardProps) {
  const hosted = HOSTED.includes(provider);
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(config?.model ?? "");
  const modelOptions = models?.models ?? [];
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);

  useEffect(() => {
    setModel(config?.model ?? "");
    setTestResult(null);
  }, [config?.model, config?.api_key_prefix_last5]);

  const canSave =
    (hosted ? apiKey.length > 0 || config?.api_key_prefix_last5 != null : true) &&
    model.length > 0 &&
    !saving;

  async function runTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.post<TestResult>(
        `/api/integrations/llm/providers/${provider}/test`,
      );
      setTestResult(result);
    } catch (e) {
      setTestResult({
        ok: false,
        model_count: null,
        error: (e as Error).message,
        error_class: "transport",
      });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="bg-white rounded-lg shadow p-5">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-lg font-semibold">{PROVIDER_LABEL[provider]}</h3>
          <div className="text-xs text-gray-500 mt-1">
            {config?.is_active
              ? "Active"
              : config?.configured
                ? "Configured (inactive)"
                : "Not configured"}
          </div>
        </div>
        <div className="space-x-2">
          {config?.configured && !config.is_active && (
            <button
              onClick={onActivate}
              className="text-bioaf-600 hover:underline text-sm"
            >
              Set Active
            </button>
          )}
          {config?.configured && (
            <button
              onClick={onDelete}
              className="text-red-600 hover:underline text-sm"
            >
              Remove
            </button>
          )}
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        {hosted && (
          <div>
            <label htmlFor="api-key" className="block text-sm font-medium text-gray-700 mb-1">
              API key
            </label>
            <input id="api-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={
                config?.api_key_prefix_last5
                  ? `*** ${config.api_key_prefix_last5}`
                  : "Paste API key"
              }
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
            />
            {config?.api_key_prefix_last5 && (
              <div className="text-xs text-gray-500 mt-1">
                Leave blank to keep the existing key.
              </div>
            )}
          </div>
        )}

        <div>
          <label htmlFor="model" className="block text-sm font-medium text-gray-700 mb-1">
            Model
          </label>
          <select id="model"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
          >
            <option value="">Select a model</option>
            {modelOptions.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          {models?.used_fallback && (
            <div className="text-xs text-amber-600 mt-1">
              Model list shown from local fallback; live fetch failed.
            </div>
          )}
        </div>
      </div>

      {config?.configured && testResult && (
        <div
          className={`mt-3 text-sm rounded p-2 ${
            testResult.ok
              ? "bg-emerald-50 border border-emerald-200 text-emerald-800"
              : "bg-red-50 border border-red-200 text-red-700"
          }`}
        >
          {testResult.ok ? (
            <>
              Connected. {testResult.model_count} model
              {testResult.model_count === 1 ? "" : "s"} available.
            </>
          ) : (
            <>
              <strong>Failed ({testResult.error_class}):</strong>{" "}
              <span className="break-all">{testResult.error}</span>
            </>
          )}
        </div>
      )}

      <div className="mt-4 flex items-center justify-end gap-2">
        {config?.configured && (
          <button
            onClick={runTest}
            disabled={testing}
            className="border border-gray-300 text-gray-700 px-4 py-2 rounded text-sm hover:bg-gray-50 disabled:opacity-50"
          >
            {testing ? "Testing..." : "Test connection"}
          </button>
        )}
        <button
          onClick={() => onSave(apiKey.length > 0 ? apiKey : null, model)}
          disabled={!canSave}
          className="bg-bioaf-600 disabled:bg-gray-300 text-white px-4 py-2 rounded text-sm"
        >
          {saving ? "Saving..." : "Save"}
        </button>
      </div>
    </div>
  );
}

function DataEgressWarningModal({
  provider,
  onConfirm,
  onCancel,
}: {
  provider: ProviderId;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl p-6 max-w-lg">
        <h3 className="text-lg font-semibold mb-2">
          Enable {PROVIDER_LABEL[provider]}?
        </h3>
        <p className="text-sm text-gray-700">
          Enabling this provider will send pipeline output data to a
          third-party LLM over the public internet. Sample metadata, JSON
          outputs, and QC reports may be transmitted. Continue?
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm border border-gray-300 rounded"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 text-sm bg-bioaf-600 text-white rounded"
          >
            Confirm and enable
          </button>
        </div>
      </div>
    </div>
  );
}

function LitReviewThresholdSection() {
  const [value, setValue] = useState<number | null>(null);
  const [input, setInput] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    literature
      .getLitReviewSettings()
      .then((s) => {
        if (cancelled) return;
        setValue(s.relevance_threshold);
        setInput(String(s.relevance_threshold));
      })
      .catch((e) => setError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, []);

  async function save() {
    setError(null);
    setSaved(false);
    const parsed = Number(input);
    if (Number.isNaN(parsed) || parsed < 0 || parsed > 1) {
      setError("Enter a number between 0.0 and 1.0.");
      return;
    }
    setSaving(true);
    try {
      const next = await literature.updateLitReviewSettings({
        relevance_threshold: parsed,
      });
      setValue(next.relevance_threshold);
      setInput(String(next.relevance_threshold));
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (value === null && !error) {
    return null;
  }

  return (
    <div className="bg-white border border-gray-200 rounded p-4 space-y-2">
      <h3 className="font-semibold text-sm">
        AI Literature Review Relevance Lower Bound
      </h3>
      <p className="text-xs text-gray-500">
        Papers scored below this threshold by the LLM are not added to the
        Library. Default 0.65. Set higher for stricter filtering, lower to see
        more candidates.
      </p>
      <div className="flex items-center gap-2">
        <input aria-label="Relevance lower bound"
          type="number"
          min={0}
          max={1}
          step={0.01}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          className="border border-gray-300 rounded px-3 py-1.5 text-sm w-28"
        />
        <button
          onClick={save}
          disabled={saving}
          className="bg-bioaf-600 text-white px-3 py-1.5 rounded text-sm hover:bg-bioaf-700 disabled:opacity-50"
        >
          {saving ? "Saving..." : "Save"}
        </button>
        {saved && !error && (
          <span className="text-xs text-green-700">Saved.</span>
        )}
      </div>
      {error && <div className="text-xs text-red-700">{error}</div>}
    </div>
  );
}

const CADENCE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
];

// Convert an ISO timestamp to the local "YYYY-MM-DDTHH:MM" a datetime-local
// input expects.
function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}`;
}

// Sensible default first-run when none is scheduled yet: one cadence from now.
function defaultFirstRunLocal(cadence: string): string {
  const d = new Date();
  if (cadence === "daily") d.setDate(d.getDate() + 1);
  else if (cadence === "monthly") d.setMonth(d.getMonth() + 1);
  else d.setDate(d.getDate() + 7);
  return toLocalInput(d.toISOString());
}

export function AutoLitReviewSection() {
  const [enabled, setEnabled] = useState(false);
  const [cadence, setCadence] = useState("weekly");
  const [maxRuns, setMaxRuns] = useState("5");
  const [firstRun, setFirstRun] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    literature
      .getLitReviewSettings()
      .then((s) => {
        if (cancelled) return;
        setEnabled(s.auto_enabled);
        setCadence(s.auto_cadence);
        setMaxRuns(String(s.max_runs_per_tick));
        setFirstRun(
          s.next_run ? toLocalInput(s.next_run) : defaultFirstRunLocal(s.auto_cadence),
        );
        setLoaded(true);
      })
      .catch((e) => setError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, []);

  async function save() {
    setError(null);
    setSaved(false);
    const cap = Number(maxRuns);
    if (!Number.isInteger(cap) || cap < 1) {
      setError("Max experiments per run must be a whole number of at least 1.");
      return;
    }
    let firstRunIso: string | undefined;
    if (enabled) {
      const when = new Date(firstRun);
      if (!firstRun || Number.isNaN(when.getTime())) {
        setError("Pick a valid first-run date and time.");
        return;
      }
      firstRunIso = when.toISOString();
    }
    setSaving(true);
    try {
      const next = await literature.updateLitReviewSettings({
        auto_enabled: enabled,
        auto_cadence: cadence,
        max_runs_per_tick: cap,
        ...(firstRunIso ? { first_run: firstRunIso } : {}),
      });
      setEnabled(next.auto_enabled);
      setCadence(next.auto_cadence);
      setMaxRuns(String(next.max_runs_per_tick));
      if (next.next_run) setFirstRun(toLocalInput(next.next_run));
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (!loaded && !error) return null;

  return (
    <div className="bg-white border border-gray-200 rounded p-4 space-y-3">
      <h3 className="font-semibold text-sm">Automated AI Literature Review</h3>
      <p className="text-xs text-gray-500">
        When enabled, bioAF runs AI Literature Review on its own for experiments
        with new samples or pipeline runs since their last automated review. New
        papers land in the Library with an AI note (dismissed papers and papers
        below the relevance lower bound are never re-recommended), and the
        affected users get an in-app notification.
      </p>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        Run AI Literature Review automatically
      </label>

      <div className="flex flex-wrap items-end gap-4">
        <div>
          <label htmlFor="cadence" className="block text-xs font-medium text-gray-700 mb-1">
            Cadence
          </label>
          <select id="cadence"
            value={cadence}
            onChange={(e) => setCadence(e.target.value)}
            disabled={!enabled}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm disabled:bg-gray-100"
          >
            {CADENCE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="first-run" className="block text-xs font-medium text-gray-700 mb-1">
            First run
          </label>
          <input id="first-run"
            type="datetime-local"
            value={firstRun}
            onChange={(e) => setFirstRun(e.target.value)}
            disabled={!enabled}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm disabled:bg-gray-100"
          />
        </div>
        <div>
          <label htmlFor="max-experiments-per-run" className="block text-xs font-medium text-gray-700 mb-1">
            Max experiments per run
          </label>
          <input id="max-experiments-per-run"
            type="number"
            min={1}
            step={1}
            value={maxRuns}
            onChange={(e) => setMaxRuns(e.target.value)}
            disabled={!enabled}
            className="border border-gray-300 rounded px-3 py-1.5 text-sm w-28 disabled:bg-gray-100"
          />
        </div>
        <button
          onClick={save}
          disabled={saving}
          className="bg-bioaf-600 text-white px-3 py-1.5 rounded text-sm hover:bg-bioaf-700 disabled:opacity-50"
        >
          {saving ? "Saving..." : "Save"}
        </button>
        {saved && !error && <span className="text-xs text-green-700">Saved.</span>}
      </div>
      {error && <div className="text-xs text-red-700">{error}</div>}
    </div>
  );
}
