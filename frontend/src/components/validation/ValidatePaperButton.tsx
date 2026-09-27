"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { useBetaFeatures } from "@/hooks/useBetaFeatures";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { RouteChooser, type ValidationRoute } from "@/components/validation/RouteChooser";

/**
 * Entry point (F2) for the validation flow from a library paper.
 *
 * **Validate starts the assessment** (plan_8_7 stage 2). Clicking it asks bioAF to read the paper and
 * assess its data, methods, code, results and interpretation, which spends no compute. The route
 * question used to stand in front of that: a reader who wanted to know what a paper's evidence says
 * had to first authorize either a deposit reproduction or hours of cluster compute, and discovery is
 * what establishes which of those is even possible, so the question was being asked before its answer
 * existed.
 *
 * **Reproducing the results is the advanced choice**, beside it, with the same chooser and the same
 * cost warnings. Choosing it authorizes what it costs, which is why it is still a separate click.
 *
 * The chooser cannot show what the paper actually HAS, because that needs the GEO accession and the
 * accession comes out of reading the paper. That check did not disappear: the driver validates the
 * choice after the read and holds the study with a plain reason if it cannot be taken.
 *
 * Gated on BOTH the `lit_validation` beta flag (so it stays hidden until the feature is switched on,
 * matching the Validation Studies nav gate) and the lit_validation:request permission.
 */
export function ValidatePaperButton({ paperId, doi }: { paperId: number; doi?: string | null }) {
  const router = useRouter();
  const { canAccess } = usePermissions();
  const { flags } = useBetaFeatures();
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // `deposit` is the default WITHIN the advanced choice, on the owner's instruction (2026-09-07):
  // start from what the authors deposited, and spend on raw reads only when a person asks for it.
  const [route, setRoute] = useState<ValidationRoute>("deposit");

  // Match the nav's beta gate. useBetaFeatures default-denies while loading, so the entry point
  // never flashes in, and never appears on an instance where the Validation Studies nav is hidden.
  if (!flags.lit_validation) return null;
  if (!canAccess("lit_validation", "request")) return null;

  async function start(chosen: "assessment" | ValidationRoute) {
    setAsking(false);
    setBusy(true);
    setError(null);
    try {
      const study = await api.post<{ id: number }>("/api/validation-studies", {
        paper_id: paperId,
        source_doi: doi ?? undefined,
        // ALWAYS send the route. The server has a default, but a caller that relies on it is one
        // default-flip away from silently spending hours of compute it did not ask for.
        intended_route: chosen,
      });
      router.push(`/lab-knowledge/validation-studies/${study.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start validation.");
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex items-center gap-2">
      <Button
        size="sm"
        onClick={() => start("assessment")}
        disabled={busy}
        title="Assess this paper's data, methods, code, results and interpretation"
      >
        {busy ? "Starting..." : "Validate findings"}
      </Button>
      <button
        type="button"
        onClick={() => setAsking(true)}
        disabled={busy}
        className="text-xs text-gray-600 underline hover:text-gray-900 disabled:opacity-50"
        title="Also reproduce the paper's results, which spends compute"
      >
        Reproduce results too
      </button>
      {error && <span className="text-sm text-red-700">{error}</span>}

      <ConfirmDialog
        open={asking}
        title="Reproduce this paper's results?"
        message={
          <div className="space-y-3">
            <p>
              The assessment of the paper&apos;s evidence runs either way. Reproducing its results also
              runs an analysis, and the two routes answer different questions.
            </p>
            <RouteChooser route={route} onChange={setRoute} idPrefix="start" />
            <p className="text-xs text-gray-500">
              Once you choose, bioAF reads the paper and starts on its own. If what you picked turns
              out not to be available for this paper, it stops and tells you before spending
              anything.
            </p>
          </div>
        }
        confirmLabel="Start validation"
        busy={busy}
        onConfirm={() => start(route)}
        onCancel={() => setAsking(false)}
      />
    </span>
  );
}
