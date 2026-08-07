"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import type { PipelineCatalog, PipelineCatalogListResponse } from "@/lib/types";
import { RegistryBrowseModal } from "@/components/pipelines/RegistryBrowseModal";
import { ErrorState } from "@/components/shared/ErrorState";

import { clickableCard } from "@/lib/a11y";

export default function PipelineCatalogPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { canAccess, loading: permsLoading } = usePermissions();

  // The catalog is a way-station: "Launch Pipeline" on an experiment sends the
  // user here with ?experiment=, and the launch wizard on the far side reads it
  // back. Losing it in the middle makes the user choose the experiment again on
  // a page they reached from that very experiment.
  const experimentParam = searchParams.get("experiment");
  const experimentId = experimentParam && /^\d+$/.test(experimentParam) ? experimentParam : null;

  function withExperiment(path: string) {
    if (!experimentId) return path;
    return `${path}${path.includes("?") ? "&" : "?"}experiment=${experimentId}`;
  }

  const [pipelines, setPipelines] = useState<PipelineCatalog[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [registryOpen, setRegistryOpen] = useState(false);

  useEffect(() => {
    loadPipelines();
  }, [router]);

  async function loadPipelines() {
    try {
      const data = await api.get<PipelineCatalogListResponse>("/api/pipelines");
      setPipelines(data.pipelines);
      setLoadError(null);
    } catch (e) {
      logError("loading the pipeline catalog", e);
      setLoadError(loadFailureMessage("The pipeline catalog"));
    } finally { setLoading(false); }
  }

  function launchPipeline(p: PipelineCatalog) {
    if (p.source_type === "custom" && p.custom_pipeline_id != null) {
      router.push(withExperiment(`/pipelines/custom/${p.custom_pipeline_id}?launch=1`));
      return;
    }
    router.push(withExperiment(`/pipelines/launch/${encodeURIComponent(p.pipeline_key)}`));
  }

  function openPipeline(p: PipelineCatalog) {
    if (p.source_type === "custom" && p.custom_pipeline_id != null) {
      router.push(withExperiment(`/pipelines/custom/${p.custom_pipeline_id}`));
      return;
    }
    router.push(withExperiment(`/pipelines/launch/${encodeURIComponent(p.pipeline_key)}`));
  }

  const canCreateCustom = !permsLoading && canAccess("custom_pipelines", "create");
  const canBrowseRegistry = !permsLoading && canAccess("pipelines", "view");
  const canInstallFromRegistry = !permsLoading && canAccess("pipelines", "create");

  return (
    <>
      <main className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <ContentLoading />
        ) : (
        <>
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold">Pipeline Catalog</h1>
            <p className="text-sm text-ink-subtle mt-1">
              Built-in NF-Core pipelines and your organization&apos;s custom pipelines.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {canBrowseRegistry && (
              <Button variant="secondary" onClick={() => setRegistryOpen(true)}>
                Search Available Pipelines
              </Button>
            )}
            {canCreateCustom && (
              <Button onClick={() => router.push("/pipelines/custom")}>
                Manage Custom Pipelines
              </Button>
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {pipelines.map((p) => {
            const isCustom = p.source_type === "custom";
            const versionLabel = isCustom
              ? p.latest_version_number != null
                ? `v${p.latest_version_number}`
                : "no versions"
              : `v${p.version || "latest"}`;
            return (
              <div
                key={p.id}
                {...clickableCard(() => openPipeline(p))}
                className="bg-surface rounded-lg shadow p-6 hover:shadow-md transition-shadow cursor-pointer"
              >
                <div className="flex items-start justify-between mb-3">
                  <h3 className="font-semibold text-lg">{p.name}</h3>
                  <span className={`px-2 py-0.5 text-xs rounded-full ${
                    isCustom
                      ? "bg-blue-100 text-blue-700"
                      : "bg-green-100 text-green-700"
                  }`}>
                    {p.source_type}
                  </span>
                </div>
                <p className="text-sm text-ink-subtle mb-4 line-clamp-2">{p.description || "No description"}</p>
                <div className="flex items-center justify-between">
                  <div className="text-xs text-ink-subtle">
                    {isCustom && p.created_by_username ? (
                      <>
                        <span>by {p.created_by_username}</span>
                        <span className="mx-1.5">•</span>
                        <span>{versionLabel}</span>
                      </>
                    ) : (
                      <span>{versionLabel}</span>
                    )}
                  </div>
                  <Button
                    size="sm"
                    onClick={(e) => { e.stopPropagation(); launchPipeline(p); }}
                    disabled={isCustom && p.latest_version_number == null}
                  >
                    Launch
                  </Button>
                </div>
              </div>
            );
          })}
          {loadError ? (
            <div className="col-span-full">
              <ErrorState message={loadError} onRetry={() => loadPipelines()} />
            </div>
          ) : null}
          {!loadError && pipelines.length === 0 && (
            <div className="col-span-full text-center py-12 text-ink-subtle">No pipelines available</div>
          )}
        </div>
        </>
        )}
      </main>
      <RegistryBrowseModal
        open={registryOpen}
        canInstall={canInstallFromRegistry}
        onClose={() => setRegistryOpen(false)}
        onInstalled={() => {
          loadPipelines();
        }}
      />
    </>
  );
}
