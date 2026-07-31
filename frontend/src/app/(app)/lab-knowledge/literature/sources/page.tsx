"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { InputDialog } from "@/components/shared/InputDialog";
import { ErrorState } from "@/components/shared/ErrorState";
import { getCurrentUser } from "@/lib/auth";
import { literature, type SourceConfig, type LiteratureSourceName } from "@/lib/literature";

const SOURCE_LABELS: Record<LiteratureSourceName, string> = {
  pubmed: "PubMed (NCBI E-utilities)",
  biorxiv: "bioRxiv",
  europepmc: "Europe PMC",
  semanticscholar: "Semantic Scholar",
};

export default function LiteratureSourcesPage() {
  const router = useRouter();
  const user = getCurrentUser();
  const canConfigure =
    user?.role_name === "admin" || user?.role_name === "comp_bio";

  const [sources, setSources] = useState<SourceConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<
    Record<string, { success: boolean; message: string; latency_ms: number }>
  >({});
  const [keyDialogSource, setKeyDialogSource] = useState<SourceConfig | null>(null);
  const [savingKey, setSavingKey] = useState(false);
  const [keyError, setKeyError] = useState<string | null>(null);

  function refresh() {
    setLoading(true);
    setError(null);
    literature
      .listSources()
      .then((data) => {
        setSources(data.items);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load sources."))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function toggleEnabled(s: SourceConfig) {
    await literature.updateSource(s.source, { enabled: !s.enabled });
    refresh();
  }
  function openKeyDialog(s: SourceConfig) {
    setKeyError(null);
    setKeyDialogSource(s);
  }
  async function saveKey(apiKey: string) {
    if (!keyDialogSource) return;
    setSavingKey(true);
    setKeyError(null);
    try {
      await literature.updateSource(keyDialogSource.source, { api_key: apiKey });
      setKeyDialogSource(null);
      refresh();
    } catch (e) {
      setKeyError(e instanceof Error ? e.message : "Could not save the key.");
    } finally {
      setSavingKey(false);
    }
  }
  async function test(s: SourceConfig) {
    const r = await literature.testSource(s.source);
    setTestResults((prev) => ({ ...prev, [s.source]: r }));
  }

  return (
    <>
        <main className="flex-1 overflow-y-auto p-6">
          <button
            onClick={() => router.push("/lab-knowledge/literature")}
            className="text-bioaf-700 hover:underline text-sm mb-4"
          >
            ← Back to library
          </button>
          <h1 className="text-2xl font-bold mb-6">Literature Sources</h1>
          {loading ? (
            <LoadingSpinner />
          ) : error ? (
            <ErrorState
              message="Couldn't load literature sources."
              details={error}
              onRetry={refresh}
            />
          ) : (
            <div className="bg-white rounded shadow divide-y">
              {sources.map((s) => {
                const result = testResults[s.source];
                return (
                  <div key={s.source} className="p-4 flex items-center gap-4">
                    <div className="flex-1">
                      <div className="font-semibold">{SOURCE_LABELS[s.source]}</div>
                      <div className="text-xs text-gray-500">
                        {s.has_api_key
                          ? "API key configured"
                          : "No API key (using unauthenticated rate limits)"}
                        {s.rate_limit_override
                          ? ` · rate override ${s.rate_limit_override} req/s`
                          : ""}
                      </div>
                      {result && (
                        <div
                          className={`text-xs mt-1 ${result.success ? "text-green-700" : "text-red-700"}`}
                        >
                          {result.success ? "✓ " : "✗ "}
                          {result.message} ({result.latency_ms} ms)
                        </div>
                      )}
                    </div>
                    <button
                      onClick={() => test(s)}
                      className="border border-gray-300 px-3 py-1 rounded text-sm hover:bg-gray-50"
                    >
                      Test
                    </button>
                    {canConfigure && (
                      <>
                        <button
                          onClick={() => openKeyDialog(s)}
                          className="border border-gray-300 px-3 py-1 rounded text-sm hover:bg-gray-50"
                        >
                          {s.has_api_key ? "Update key" : "Set key"}
                        </button>
                        <label className="flex items-center gap-2 text-sm">
                          <input
                            type="checkbox"
                            checked={s.enabled}
                            onChange={() => toggleEnabled(s)}
                          />
                          Enabled
                        </label>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </main>
      <InputDialog
        open={keyDialogSource !== null}
        title={keyDialogSource ? `API key for ${SOURCE_LABELS[keyDialogSource.source]}` : ""}
        message="Stored encrypted on the server. Leave the field empty and save to clear the current key."
        label="API key"
        type="password"
        placeholder="Paste the key"
        allowEmpty
        confirmLabel="Save key"
        busyLabel="Saving..."
        busy={savingKey}
        error={keyError}
        onConfirm={saveKey}
        onCancel={() => setKeyDialogSource(null)}
      />
    </>
  );
}
