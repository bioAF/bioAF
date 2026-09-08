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
 * **The route is asked HERE** (owner decision, 2026-09-08), not at the C1 gate. Clicking used to
 * create the study immediately and drop the reader on its detail page showing "Requested - Step 1 of
 * 9" with an in-progress badge, while TWO manual clicks ("Read paper", then "Approve") stood between
 * that page and any work. Both stops looked like progress, so a study could sit untouched
 * indefinitely while the page implied it was working.
 *
 * So: one question, asked once, before anything exists. The study is created carrying the answer and
 * the driver takes it from there. Nothing is created if the reader cancels.
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
  // `deposit` is the default on the owner's instruction (2026-09-07): start from what the authors
  // deposited, and spend on raw reads only when a person asks for it.
  const [route, setRoute] = useState<ValidationRoute>("deposit");

  // Match the nav's beta gate. useBetaFeatures default-denies while loading, so the entry point
  // never flashes in, and never appears on an instance where the Validation Studies nav is hidden.
  if (!flags.lit_validation) return null;
  if (!canAccess("lit_validation", "request")) return null;

  async function start() {
    setAsking(false);
    setBusy(true);
    setError(null);
    try {
      const study = await api.post<{ id: number }>("/api/validation-studies", {
        paper_id: paperId,
        source_doi: doi ?? undefined,
        // ALWAYS send the route. The server has a default, but a caller that relies on it is one
        // default-flip away from silently spending hours of compute it did not ask for.
        intended_route: route,
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
        onClick={() => setAsking(true)}
        disabled={busy}
        title="Reproduce this paper's findings and compare the results"
      >
        {busy ? "Starting..." : "Validate findings"}
      </Button>
      {error && <span className="text-sm text-red-700">{error}</span>}

      <ConfirmDialog
        open={asking}
        title="What should we validate?"
        message={
          <div className="space-y-3">
            <p>Choose what to validate. The two routes answer different questions.</p>
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
        onConfirm={start}
        onCancel={() => setAsking(false)}
      />
    </span>
  );
}
