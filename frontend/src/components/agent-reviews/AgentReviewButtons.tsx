"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { SectionBuilderModal } from "./SectionBuilderModal";

interface AgentReviewButtonsProps {
  mode: "pipeline_run" | "experiment";
  runId?: number;
  experimentId: number | null;
  pipelineStatus?: string;
  onTriggered?: () => void;
}

export function AgentReviewButtons({
  mode,
  runId,
  experimentId,
  pipelineStatus,
  onTriggered,
}: AgentReviewButtonsProps) {
  const { canAccess } = usePermissions();
  const canUse = canAccess("llm_integration", "use");
  // Track which provider is active, not just whether one exists, so we can
  // hide the buttons when Gemma is active. The self-hosted Gemma dispatch
  // is stubbed in v1, so triggering a review with Gemma active leaves the
  // job in 'pending' forever. Hiding the buttons protects users from that.
  const [activeProvider, setActiveProvider] = useState<string | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!canUse) return;
    api
      .get<{ active_provider: string | null }>("/api/integrations/llm/providers")
      .then((d) => setActiveProvider(d.active_provider))
      .catch(() => setActiveProvider(null));
  }, [canUse]);

  if (!canUse) return null;
  if (activeProvider === undefined) return null;
  if (activeProvider === null) return null;
  if (activeProvider === "gemma") return null;
  if (mode === "pipeline_run" && pipelineStatus !== "completed") return null;
  if (mode === "experiment" && experimentId === null) return null;

  const label =
    mode === "pipeline_run" ? "Review this pipeline run" : "Review this Experiment";

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={() => setOpen(true)}
        className="px-3 py-1.5 text-sm bg-bioaf-600 hover:bg-bioaf-700 text-white rounded"
      >
        {label}
      </button>
      {error && (
        <span className="text-red-600 text-sm" role="alert">
          {error}
        </span>
      )}
      {open && (
        <SectionBuilderModal
          entityType={mode}
          runId={mode === "pipeline_run" ? runId : undefined}
          experimentId={experimentId}
          onCancel={() => setOpen(false)}
          onSubmitted={() => {
            setOpen(false);
            onTriggered?.();
          }}
          onError={(m) => setError(m)}
        />
      )}
    </div>
  );
}
