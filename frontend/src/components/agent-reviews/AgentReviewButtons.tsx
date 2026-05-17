"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { SectionBuilderModal } from "./SectionBuilderModal";

interface AgentReviewButtonsProps {
  runId: number;
  experimentId: number | null;
  pipelineStatus: string;
  onTriggered?: () => void;
}

export function AgentReviewButtons({
  runId,
  experimentId,
  pipelineStatus,
  onTriggered,
}: AgentReviewButtonsProps) {
  const { canAccess } = usePermissions();
  const canUse = canAccess("llm_integration", "use");
  const [hasActiveProvider, setHasActiveProvider] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openMode, setOpenMode] = useState<"a" | "b" | null>(null);

  useEffect(() => {
    if (!canUse) return;
    api
      .get<{ active_provider: string | null }>("/api/integrations/llm/providers")
      .then((d) => setHasActiveProvider(d.active_provider != null))
      .catch(() => setHasActiveProvider(false));
  }, [canUse]);

  if (!canUse) return null;
  if (pipelineStatus !== "completed") return null;
  if (hasActiveProvider === false) return null;

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={() => setOpenMode("a")}
        className="px-3 py-1.5 text-sm border border-bioaf-600 text-bioaf-700 rounded hover:bg-bioaf-50"
      >
        Review this pipeline run
      </button>
      {experimentId !== null && (
        <button
          onClick={() => setOpenMode("b")}
          className="px-3 py-1.5 text-sm border border-bioaf-600 text-bioaf-700 rounded hover:bg-bioaf-50"
        >
          Review across experiment
        </button>
      )}
      {error && (
        <span className="text-red-600 text-sm" role="alert">
          {error}
        </span>
      )}
      {openMode !== null && (
        <SectionBuilderModal
          entityType={openMode === "a" ? "pipeline_run" : "experiment"}
          runId={runId}
          experimentId={experimentId}
          onCancel={() => setOpenMode(null)}
          onSubmitted={() => {
            setOpenMode(null);
            onTriggered?.();
          }}
          onError={(m) => setError(m)}
        />
      )}
    </div>
  );
}
