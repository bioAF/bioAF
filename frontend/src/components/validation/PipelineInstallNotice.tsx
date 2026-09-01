"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { logError } from "@/lib/errorReporting";

/**
 * States, before approval, that the plan's pipeline is not installed on this bioAF, and offers to
 * install it.
 *
 * Nothing said this before. A plan naming a pipeline the instance did not have was approved, the
 * data-acquisition run downloaded the whole dataset, and only then did the launch refuse with
 * "Pipeline not found or not enabled" - after the compute was spent, in a state the scientist could
 * do nothing about.
 *
 * It matters more now that a paper can map to any pipeline in the nf-core registry rather than to
 * one of six: the common case is a real pipeline this lab simply has not installed yet, which is a
 * one-click problem, not a dead end.
 */
export function PipelineInstallNotice({
  pipelineKey,
  pipelineVersion,
  registryName,
  installed,
  onInstalled,
}: {
  pipelineKey?: string | null;
  pipelineVersion?: string | null;
  // The bare nf-core name the install endpoint takes (`ampliseq`), resolved server-side. Null for a
  // pipeline that did not come from the registry, which cannot be installed this way.
  registryName?: string | null;
  // null when the plan names no pipeline: there is nothing to install and nothing to say.
  installed?: boolean | null;
  onInstalled: () => void;
}) {
  const { canAccess } = usePermissions();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!pipelineKey || installed !== false) return null;

  const canInstall = canAccess("pipelines", "create") && !!registryName && !!pipelineVersion;

  async function install() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/pipelines/registry/${registryName}/install`, { version: pipelineVersion });
      onInstalled();
    } catch (e) {
      logError(`installing ${pipelineKey}`, e);
      setError(e instanceof Error ? e.message : "The pipeline could not be installed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded border border-amber-200 bg-amber-50 px-4 py-3">
      <p className="text-sm font-semibold text-amber-900">
        {pipelineKey}
        {pipelineVersion ? ` ${pipelineVersion}` : ""} is not installed on this bioAF
      </p>
      <p className="mt-1 text-xs text-amber-800">
        This study cannot run until it is. Installing it now avoids downloading the paper&apos;s data
        first and finding out afterwards.
      </p>
      {canInstall ? (
        <button
          type="button"
          onClick={install}
          disabled={busy}
          className="mt-3 rounded bg-amber-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy ? "Installing..." : `Install ${pipelineKey}`}
        </button>
      ) : (
        <p className="mt-2 text-xs text-amber-800">
          Ask an administrator to install {pipelineKey} from Pipelines.
        </p>
      )}
      {error && <p className="mt-2 text-xs text-red-700">{error}</p>}
    </div>
  );
}
