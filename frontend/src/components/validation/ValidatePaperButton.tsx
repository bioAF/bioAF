"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";

/**
 * Entry point (F2) for the validation flow from a library paper: creates a ValidationStudy for the
 * paper and opens its detail page, where the reader drives Read -> Approve -> Classify. Gated on the
 * lit_validation:request permission; renders nothing without it.
 */
export function ValidatePaperButton({ paperId, doi }: { paperId: number; doi?: string | null }) {
  const router = useRouter();
  const { canAccess } = usePermissions();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!canAccess("lit_validation", "request")) return null;

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const study = await api.post<{ id: number }>("/api/validation-studies", {
        paper_id: paperId,
        source_doi: doi ?? undefined,
      });
      router.push(`/validation-studies/${study.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start validation.");
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex items-center gap-2">
      <button
        onClick={start}
        disabled={busy}
        className="rounded bg-bioaf-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-bioaf-700 disabled:opacity-50"
        title="Reproduce this paper's analysis and compare the results"
      >
        {busy ? "Starting..." : "Validate reproduction"}
      </button>
      {error && <span className="text-sm text-red-700">{error}</span>}
    </span>
  );
}
