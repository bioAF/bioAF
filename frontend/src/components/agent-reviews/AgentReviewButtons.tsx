"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { SectionBuilderModal } from "./SectionBuilderModal";
import { Button } from "@/components/ui/Button";

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
  // Whether AI Review can actually run for this org (an active, non-gemma
  // provider with a model). Sourced from the availability endpoint, which is
  // readable by anyone who can use AI Review, unlike the admin-only providers
  // endpoint. undefined while loading.
  const [enabled, setEnabled] = useState<boolean | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!canUse) return;
    api
      .get<{ enabled: boolean }>("/api/agent_reviews/availability")
      .then((d) => setEnabled(d.enabled))
      .catch(() => setEnabled(false));
  }, [canUse]);

  if (!canUse) return null;
  if (enabled === undefined) return null;
  if (!enabled) return null;
  if (mode === "pipeline_run" && pipelineStatus !== "completed") return null;
  if (mode === "experiment" && experimentId === null) return null;

  const label =
    mode === "pipeline_run" ? "Review this pipeline run" : "Review this Experiment";

  return (
    <div className="flex items-center gap-2">
      <Button size="sm"
        onClick={() => setOpen(true)}>
        {label}
      </Button>
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
