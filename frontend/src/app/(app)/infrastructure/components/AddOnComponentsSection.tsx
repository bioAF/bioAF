"use client";

import { useCallback, useRef, useState } from "react";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { useToast } from "@/components/shared/Toast";
import { useConfirm } from "@/hooks/useConfirm";
import { useInfrastructureActions } from "@/hooks/useInfrastructureActions";
import { useResource } from "@/hooks/useResource";
import { invalidateComponentCache } from "@/hooks/useComponents";
import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";
import type { ComponentsData } from "./infrastructureTypes";

const CATEGORY_LABELS: Record<string, string> = {
  pipeline_orchestration: "Pipeline Orchestration",
  analysis: "Analysis",
  visualization: "Visualization",
  search: "Search",
};

const CATEGORY_ORDER = [
  "pipeline_orchestration",
  "analysis",
  "visualization",
  "search",
];

/**
 * The add-on components: enable, disable, rebuild, retry or cancel an image build. Each change
 * needs infrastructure `configure`, keeps its confirmation, and asks the page to reload when it
 * lands. Build status is polled here while a component builds.
 */
export function AddOnComponentsSection({
  componentsData,
  onChanged,
}: {
  componentsData: ComponentsData | null;
  /** Something changed on the server; the page reloads its data. */
  onChanged: () => void;
}) {
  const confirm = useConfirm();
  const toast = useToast();
  const actions = useInfrastructureActions();
  const configureReason = actions.reason("configure");
  const [componentErrors, setComponentErrors] = useState<Record<string, string>>({});
  const [togglingComponent, setTogglingComponent] = useState<string | null>(null);
  const [pendingRebuild, setPendingRebuild] = useState<string | null>(null);
  const [cancellingBuild, setCancellingBuild] = useState(false);

  // Fetch build status when any component is provisioning or build_failed
  const buildRelatedComponents = componentsData?.components.filter(
    (c) => c.status === "provisioning" || c.status === "build_failed",
  ) ?? [];
  const hasBuildRelated = buildRelatedComponents.length > 0;

  // Map component keys to their build status endpoints
  const notebookKeys = new Set(["rstudio", "jupyterhub"]);
  const cellxgeneKeys = new Set(["cellxgene"]);

  // Which build-status endpoints apply; the read is keyed by them, so a page-data reload that
  // leaves them (and the components' statuses) unchanged does not start a new read. That matters: a finished build asks for a
  // page-data reload, and when the reload re-triggered this read, a build that had already
  // finished (or failed) reloaded the page in a tight loop.
  const buildEndpoints: { type: string; url: string }[] = [];
  if (buildRelatedComponents.some((c) => notebookKeys.has(c.key))) {
    buildEndpoints.push({ type: "notebook", url: "/api/v1/infrastructure/notebook-image/build-status" });
  }
  if (buildRelatedComponents.some((c) => cellxgeneKeys.has(c.key))) {
    buildEndpoints.push({ type: "cellxgene", url: "/api/v1/infrastructure/cellxgene-image/build-status" });
  }
  // The related components' statuses are part of the key, so a Retry Build (failed ->
  // provisioning) reads at once, while a reload that changes nothing does not.
  const buildKey = hasBuildRelated
    ? `build-status:${buildEndpoints.map((ep) => ep.type).join(",")}:${buildRelatedComponents
        .map((c) => `${c.key}=${c.status}`)
        .join(",")}`
    : null;
  const buildEndpointsRef = useRef(buildEndpoints);
  buildEndpointsRef.current = buildEndpoints;
  const isProvisioning = componentsData?.components.some((c) => c.status === "provisioning") ?? false;

  // Polls every 15 s, after the previous read settles, while a component is provisioning; a
  // build_failed component alone is read once. A failed read keeps the last statuses.
  const buildPoll = useResource(
    buildKey,
    useCallback(async (signal: AbortSignal) => {
      const results: Record<string, { build_id: string | null; build_status: string | null; image_uri: string | null }> = {};
      for (const ep of buildEndpointsRef.current) {
        results[ep.type] = await api.get<{
          build_id: string | null;
          build_status: string | null;
          image_uri: string | null;
        }>(ep.url, { signal });
      }
      return results;
    }, []),
    {
      poll: isProvisioning ? 15000 : false,
      onSuccess: (results) => {
        const anyFinished = Object.values(results).some(
          (status) => status.build_status && !["WORKING", "QUEUED"].includes(status.build_status),
        );
        if (anyFinished) {
          onChanged();
        }
      },
      onError: (e) => logError("reading image build status", e),
    },
  );

  // The statuses shown are the latest answer for the current build key (none when nothing builds).
  const buildStatusMap = buildPoll.data ?? {};

  // Components that have a built image
  const imageComponents = new Set(["rstudio", "jupyterhub", "cellxgene"]);

  async function handleComponentToggle(componentKey: string, rebuildChoice?: boolean) {
    if (!actions.allowed("configure")) return;
    setComponentErrors((prev) => ({ ...prev, [componentKey]: "" }));

    // When re-enabling an image component, check if it already has a
    // successful build and ask the user whether to rebuild.
    const comp = componentsData?.components.find((c) => c.key === componentKey);
    const isEnabling = comp && comp.status === "disabled";
    let forceRebuild = rebuildChoice ?? false;

    if (rebuildChoice === undefined && isEnabling && imageComponents.has(componentKey)) {
      // Check if there's already a successful image for this component
      const buildType = cellxgeneKeys.has(componentKey) ? "cellxgene" : "notebook";
      const statusUrl = buildType === "cellxgene"
        ? "/api/v1/infrastructure/cellxgene-image/build-status"
        : "/api/v1/infrastructure/notebook-image/build-status";
      try {
        const status = await api.get<{
          build_id: string | null;
          build_status: string | null;
          image_uri: string | null;
        }>(statusUrl);
        if (status.image_uri) {
          // Hand off to the dialog and stop here. It re-enters this function with
          // an explicit choice, so dismissing it genuinely does nothing.
          setPendingRebuild(componentKey);
          return;
        }
      } catch {
        // No existing build -- proceed normally
      }
    }

    // Disabling had no gate while ENABLING opens a three-way dialog above. The
    // asymmetry ran the wrong way: re-enabling is recoverable, tearing down a
    // deployed add-on takes any sessions running on it with it.
    if (comp && comp.status === "enabled") {
      const ok = await confirm({
        title: `Disable ${comp.name}?`,
        message: (
          <>
            <p>
              This tears down the deployed {comp.name} component.{" "}
              <span className="font-medium">Any sessions currently running on it stop</span>, and
              unsaved work in them is lost.
            </p>
            <p>
              Files in storage are not affected, and you can enable it again later, though that
              means provisioning it from scratch.
            </p>
          </>
        ),
        confirmLabel: "Disable",
        variant: "danger",
      });
      if (!ok) return;
    }
    // Permission is checked again: it may have changed while a confirmation was open.
    if (!actions.stillAllowed("configure")) return;

    setTogglingComponent(componentKey);
    try {
      const url = forceRebuild
        ? `/api/v1/infrastructure/stack/components/${componentKey}/toggle?force_rebuild=true`
        : `/api/v1/infrastructure/stack/components/${componentKey}/toggle`;
      await api.post(url);
      invalidateComponentCache();
      onChanged();
    } catch (e) {
      logError(`toggling the ${componentKey} component`, e);
      // A 4xx here is a validation message the backend wrote FOR the admin, e.g.
      // "Dependency not met: kubernetes_cluster must be enabled first". That is
      // already the plain sentence, and it names the fix. Replacing it with the
      // generic house sentence threw away the only useful part, so the rule is
      // scoped: show the server's reason when it gave one, otherwise say nothing
      // technical.
      const status = (e as { status?: number } | null)?.status;
      const reason = status && status >= 400 && status < 500 && e instanceof Error ? e.message : null;
      setComponentErrors((prev) => ({
        ...prev,
        [componentKey]:
          reason ?? "That change could not be applied. The technical detail is in the application logs.",
      }));
    } finally {
      setTogglingComponent(null);
    }
  }



  async function handleCancelBuild() {
    if (!actions.allowed("configure")) return;
    setCancellingBuild(true);
    try {
      await api.post("/api/v1/infrastructure/notebook-image/cancel");
      onChanged();
    } catch (e) {
      const message = e instanceof Error ? e.message : "Cancel failed";
      toast.error(message);
    } finally {
      setCancellingBuild(false);
    }
  }

  async function handleRetryBuild(componentKey: string) {
    if (!actions.allowed("configure")) return;
    setTogglingComponent(componentKey);
    try {
      // Disable then re-enable to trigger a fresh build
      await api.post(`/api/v1/infrastructure/stack/components/${componentKey}/toggle`);
      await api.post(`/api/v1/infrastructure/stack/components/${componentKey}/toggle`);
      invalidateComponentCache();
      onChanged();
    } catch (e) {
      const message = e instanceof Error ? e.message : "Retry failed";
      setComponentErrors((prev) => ({ ...prev, [componentKey]: message }));
    } finally {
      setTogglingComponent(null);
    }
  }

  return (
    <>
    {/* Add-on Components */}
    {componentsData && componentsData.components.length > 0 && (
      <div className="space-y-6 mb-8">
        {CATEGORY_ORDER.filter((cat) =>
          componentsData.components.some((c) => c.category === cat)
        ).map((category) => (
          <div key={category}>
            <h2 className="text-lg font-semibold mb-3">
              {CATEGORY_LABELS[category] ?? category}
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {componentsData.components
                .filter((c) => c.category === category)
                .map((comp) => comp.status === "coming_soon" ? (
                  <div
                    key={comp.key}
                    className="bg-white rounded-lg shadow p-5 border border-gray-200 opacity-60"
                  >
                    <div className="flex items-start justify-between mb-2">
                      <h3 className="font-semibold text-sm text-gray-500">{comp.name}</h3>
                      <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full font-medium">
                        Coming Soon
                      </span>
                    </div>
                    <p className="text-xs text-gray-500">{comp.description}</p>
                  </div>
                ) : (
                  <div
                    key={comp.key}
                    className="bg-white rounded-lg shadow p-5 border border-gray-200"
                  >
                    {(() => {
                      // Resolve the right build status for this component
                      const buildType = cellxgeneKeys.has(comp.key) ? "cellxgene" : "notebook";
                      const buildStatus = buildStatusMap[buildType] ?? null;
                      return (<>
                    <div className="flex items-start justify-between mb-2">
                      <h3 className="font-semibold text-sm">{comp.name}</h3>
                      {(() => {
                        const buildFailed = comp.status === "build_failed" ||
                          (comp.status === "provisioning" && buildStatus && ["FAILURE", "CANCELLED", "TIMEOUT"].includes(buildStatus.build_status ?? ""));
                        return (
                          <span
                            className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                              comp.status === "enabled"
                                ? "bg-green-100 text-green-700"
                                : buildFailed
                                  ? "bg-red-100 text-red-700"
                                  : comp.status === "provisioning"
                                    ? "bg-amber-100 text-amber-700"
                                    : comp.status === "queued_for_infra"
                                      ? "bg-blue-100 text-blue-700"
                                      : "bg-gray-100 text-gray-600"
                            }`}
                          >
                            {comp.status === "enabled"
                              ? "Enabled"
                              : buildFailed
                                ? "Build Failed"
                                : comp.status === "provisioning"
                                  ? "Building Image..."
                                  : comp.status === "queued_for_infra"
                                    ? "Queued"
                                    : "Disabled"}
                          </span>
                        );
                      })()}
                    </div>
                    <p className="text-xs text-gray-600 mb-3">{comp.description}</p>
                    {comp.dependencies.length > 0 && (
                      <p className="text-xs text-gray-500 mb-2">
                        Requires: {comp.dependencies.join(", ")}
                      </p>
                    )}
                    {/* No answer yet: the building panel below needs one, so a failed first read is said here. */}
                    {comp.status === "provisioning" && !buildStatus && buildPoll.error !== null && (
                      <p data-testid="build-status-load-failed" role="status" className="text-xs text-red-600 mb-2">
                        Image build status is unavailable. bioAF will keep checking.
                      </p>
                    )}
                    {/* Show building panel only when actually building (not when build already failed) */}
                    {comp.status === "provisioning" && buildStatus && !["FAILURE", "CANCELLED", "TIMEOUT"].includes(buildStatus.build_status ?? "") && (
                      <div className="bg-amber-50 border border-amber-200 rounded p-2 mb-2">
                        <p className="text-xs font-medium text-amber-800">
                          Building image...
                        </p>
                        <p className="text-xs text-amber-700 mt-0.5">
                          Status: {buildStatus.build_status ?? "Starting"}
                          {buildStatus.build_id && (
                            <span className="text-amber-400 ml-1">
                              ({buildStatus.build_id.slice(0, 8)})
                            </span>
                          )}
                        </p>
                        <p className="text-xs text-amber-500 mt-0.5">
                          This one-time setup can take up to an hour. You can leave this page.
                        </p>
                        {buildPoll.error !== null && (
                          <p data-testid="build-status-unavailable" role="status" className="text-xs text-red-600 mt-0.5">
                            Build status could not be refreshed. bioAF keeps checking.
                          </p>
                        )}
                        <button
                          onClick={handleCancelBuild}
                          disabled={cancellingBuild || configureReason !== null}
                          title={configureReason ?? undefined}
                          className="mt-1 text-xs text-red-600 hover:text-red-800 font-medium"
                        >
                          {cancellingBuild ? "Cancelling..." : "Cancel Build"}
                        </button>
                      </div>
                    )}
                    {/* Show failure panel for build_failed OR provisioning-but-actually-failed */}
                    {(comp.status === "build_failed" || (comp.status === "provisioning" && buildStatus && ["FAILURE", "CANCELLED", "TIMEOUT"].includes(buildStatus.build_status ?? ""))) && (
                      <div className="bg-red-50 border border-red-200 rounded p-2 mb-2">
                        <p className="text-xs font-medium text-red-800">
                          Image build failed
                        </p>
                        {buildStatus?.build_id && (
                          <p className="text-xs text-red-600 mt-0.5">
                            Build: {buildStatus.build_id.slice(0, 8)} -- {buildStatus.build_status}
                          </p>
                        )}
                        <button
                          onClick={() => handleRetryBuild(comp.key)}
                          disabled={togglingComponent === comp.key || configureReason !== null}
                          title={configureReason ?? undefined}
                          className="mt-1 text-xs text-bioaf-600 hover:text-bioaf-700 font-medium"
                        >
                          {togglingComponent === comp.key ? "Retrying..." : "Retry Build"}
                        </button>
                      </div>
                    )}
                    <div className="flex items-center justify-end mt-3">
                      <button
                        onClick={() => handleComponentToggle(comp.key)}
                        disabled={comp.status === "provisioning" || comp.status === "build_failed" || togglingComponent === comp.key || configureReason !== null}
                        title={configureReason ?? undefined}
                        className={`px-3 py-1 text-xs rounded font-medium ${
                          togglingComponent === comp.key
                            ? "bg-gray-200 text-gray-500 cursor-not-allowed"
                            : comp.status === "enabled"
                              ? "bg-red-50 text-red-700 hover:bg-red-100"
                              : comp.status === "provisioning"
                                ? "bg-amber-100 text-amber-700 cursor-not-allowed"
                                : comp.status === "build_failed"
                                  ? "bg-gray-200 text-gray-500 cursor-not-allowed"
                                  : "bg-bioaf-600 text-white hover:bg-bioaf-700"
                        }`}
                      >
                        {togglingComponent === comp.key
                          ? "Updating..."
                          : comp.status === "enabled"
                            ? "Disable"
                            : comp.status === "provisioning"
                              ? "Building..."
                              : comp.status === "build_failed"
                                ? "Failed"
                                : "Enable"}
                      </button>
                    </div>
                    {componentErrors[comp.key] && (
                      <p className="text-xs text-red-600 mt-2">
                        {componentErrors[comp.key]}
                      </p>
                    )}
                    </>);
                    })()}
                  </div>
                ))}
            </div>
          </div>
        ))}
      </div>
    )}
    <ConfirmDialog
      open={pendingRebuild !== null}
      title="Rebuild the image?"
      message={
        <>
          <p>
            An image for this component already exists. You can rebuild it with the
            latest definition, or enable the component using the image that is
            already there.
          </p>
          <p>Rebuilding takes several minutes. Nothing happens if you cancel.</p>
        </>
      }
      confirmLabel="Rebuild"
      secondaryLabel="Use existing image"
      cancelLabel="Cancel"
      onConfirm={() => {
        const key = pendingRebuild;
        setPendingRebuild(null);
        if (key) handleComponentToggle(key, true);
      }}
      onSecondary={() => {
        const key = pendingRebuild;
        setPendingRebuild(null);
        if (key) handleComponentToggle(key, false);
      }}
      onCancel={() => setPendingRebuild(null)}
    />
    </>
  );
}
