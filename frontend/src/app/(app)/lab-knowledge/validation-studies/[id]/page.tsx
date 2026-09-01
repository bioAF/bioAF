"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter, useParams } from "next/navigation";
import { Breadcrumb } from "@/components/layout/Breadcrumb";
import Link from "next/link";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ValidationStudyOutcome } from "@/components/validation/ValidationStudyOutcome";
import { ValidationStudyActions } from "@/components/validation/ValidationStudyActions";
import { ValidationVerdictPanel } from "@/components/validation/ValidationVerdictPanel";
import { ValidationEvidenceTable, type Evidence } from "@/components/validation/ValidationEvidenceTable";
import {
  Level3Gate,
  type DifferentialDesign,
  type FindingClaim,
} from "@/components/validation/Level3Gate";
import { PipelineInstallNotice } from "@/components/validation/PipelineInstallNotice";
import { DepositConflictNotice, type DepositConflict } from "@/components/validation/DepositConflictNotice";
import { Level3ResultPanel } from "@/components/validation/Level3ResultPanel";
import { RetryNotice } from "@/components/validation/RetryNotice";
import { SamplesMismatchNotice } from "@/components/validation/SamplesMismatchNotice";
import { ProvenanceExportMenu } from "@/components/shared/ProvenanceExportMenu";
import { ErrorState } from "@/components/shared/ErrorState";
import { LitValidationDisabledNotice } from "@/components/validation/LitValidationGate";
import { useBetaFeatures } from "@/hooks/useBetaFeatures";
import { api } from "@/lib/api";

// States the background driver advances on its own; while a study sits in one, poll so the page
// reflects progress toward the next human gate (plan_ready / comparing) or a terminal state.
const ADVANCING_STATES = new Set(["acquiring_data", "setup", "running", "extracting"]);

// Before the paper is read there is no reproduction plan/evidence to report on, so the F3 export
// control is hidden until the study has advanced past the pre-comprehension states.
const PRE_REPORT_STATES = new Set(["requested", "acquiring_text", "reading"]);

interface ReproductionPlanView {
  pipeline_key?: string | null;
  pipeline_version?: string | null;
  accessions?: string[] | null;
  reference_genome?: string | null;
  reference_build?: string | null;
  mapping_confidence?: string | null;
  mapping_notes?: string | null;
  blockers?: string[] | null;
  differential_design?: DifferentialDesign | null;
  finding_claim?: FindingClaim | null;
  // The tools the paper's own methods named, and which finding kinds this pipeline can reproduce.
  tools?: string[] | null;
  supported_finding_kinds?: string[] | null;
  // Whether this bioAF actually holds the plan's pipeline, and the bare registry name to install it
  // by. Computed server-side per request: a pipeline can be installed between writing a plan and
  // approving it.
  pipeline_installed?: boolean | null;
  pipeline_registry_name?: string | null;
  // The one blocker that refuses approval, and the pipeline that would resolve it. Computed per
  // request, so a plan corrected in another tab stops showing it.
  deposit_conflict?: DepositConflict | null;
}

interface ValidationStudy {
  id: number;
  state: string;
  // Server-resolved display title (paper.title -> DOI -> accession -> "Study #{id}").
  title?: string | null;
  classification?: string | null;
  confidence?: number | null;
  paper_id?: number | null;
  source_doi?: string | null;
  source_accession?: string | null;
  experiment_id?: number | null;
  failure_reason?: string | null;
  plan?: ReproductionPlanView | null;
  evidence?: Evidence | null;
}

/**
 * F1 study view: fetches one validation study and renders its outcome, reproduction plan, and the
 * computed-vs-claimed evidence. The outcome is gated on state (a validation badge only once the study
 * is `classified`; otherwise the pipeline stage), so a running study never reads as "Could Not
 * Reproduce". Comparison is manual in Phase 1: the evidence table is what a scientist reads to classify.
 */
export default function ValidationStudyPage() {
  const router = useRouter();
  const params = useParams();
  const id = params.id as string;

  const [study, setStudy] = useState<ValidationStudy | null>(null);
  const [loading, setLoading] = useState(true);
  const { flags, loading: betaLoading } = useBetaFeatures();

  const refresh = useCallback(async () => {
    try {
      const data = await api.get<ValidationStudy>(`/api/validation-studies/${id}`);
      setStudy(data);
    } catch {
      setStudy((prev) => prev ?? null);
    }
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await refresh();
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh, router]);

  // While the driver is advancing the study on its own, poll so the stage/evidence stay current.
  useEffect(() => {
    if (!study || !ADVANCING_STATES.has(study.state)) return;
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [study, refresh]);

  const retry = useCallback(async () => {
    setLoading(true);
    await refresh();
    setLoading(false);
  }, [refresh]);

  if (loading || betaLoading) {
    return (
      <main className="flex flex-1 items-center justify-center p-6">
        <LoadingSpinner size="lg" />
      </main>
    );
  }

  // Match the nav + entry-button beta gate: a flag-off user reaching this URL directly gets the
  // "not enabled" notice, not the study.
  if (!flags.lit_validation) {
    return (
      <main className="flex flex-1 items-center justify-center p-6">
        <LitValidationDisabledNotice />
      </main>
    );
  }

  if (!study) {
    return (
      <main className="flex-1 overflow-y-auto p-6">
        <ErrorState
          message="Validation study not found, or it could not be loaded."
          onRetry={retry}
        />
      </main>
    );
  }

  const plan = study.plan;
  const fallbackTitle = `Study #${study.id}`;
  const displayTitle = study.title || fallbackTitle;

  return (
    <>
      <Breadcrumb entityName={displayTitle} />
      <main className="flex-1 overflow-y-auto p-6">
        <div className="mb-6 flex flex-wrap items-center gap-4">
          <button onClick={() => router.back()} className="text-gray-500 hover:text-gray-700">
            ← Back
          </button>
          <h1 className="text-2xl font-bold">{displayTitle}</h1>
          {study.title && study.title !== fallbackTitle && (
            <span className="font-mono text-sm text-gray-500" title="Validation study id">
              #{study.id}
            </span>
          )}
          {study.source_doi && (
            <a
              href={`https://doi.org/${study.source_doi}`}
              target="_blank"
              rel="noreferrer"
              className="rounded bg-blue-50 px-2 py-0.5 font-mono text-sm text-blue-700 hover:underline"
              title="Source DOI"
            >
              {study.source_doi}
            </a>
          )}
          {study.source_accession && (
            <span className="rounded bg-gray-100 px-2 py-0.5 font-mono text-sm text-gray-600" title="Source accession">
              {study.source_accession}
            </span>
          )}
          {study.paper_id && (
            <Link
              href={`/lab-knowledge/literature/papers/${study.paper_id}`}
              className="rounded bg-bioaf-50 px-2 py-0.5 text-sm text-bioaf-700 hover:underline"
              title="Open this paper in the Literature library"
            >
              Source paper
            </Link>
          )}
          {!PRE_REPORT_STATES.has(study.state) && (
            <div className="ml-auto">
              <ProvenanceExportMenu entityType="validation-studies" entityId={study.id} label="Export Report" />
            </div>
          )}
        </div>

        <section className="mb-6">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500">Outcome</h2>
          <ValidationStudyOutcome
            state={study.state}
            confidence={study.confidence}
            classification={study.classification}
            failureReason={study.failure_reason}
          />
        </section>

        {study.evidence?.classification_result && (
          <section className="mb-6">
            <ValidationVerdictPanel
              result={study.evidence.classification_result}
              level3Skipped={study.evidence.level3_skipped}
              level3Failed={study.evidence.level3_failed}
            />
          </section>
        )}

        {plan && (
          <section className="mb-6">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500">
              Reproduction plan
            </h2>
            <dl className="grid grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
              <Field label="Pipeline">
                {plan.pipeline_key
                  ? `${plan.pipeline_key}${plan.pipeline_version ? ` ${plan.pipeline_version}` : ""}`
                  : "-"}
              </Field>
              <Field label="Reference genome">{plan.reference_genome || "-"}</Field>
              <Field label="Accessions">
                {plan.accessions && plan.accessions.length > 0 ? plan.accessions.join(", ") : "-"}
              </Field>
              <Field label="Mapping confidence">{plan.mapping_confidence || "-"}</Field>
            </dl>
            {plan.blockers && plan.blockers.length > 0 && (
              <div className="mt-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-amber-700">Blockers</p>
                <ul className="mt-1 list-inside list-disc text-sm text-gray-700">
                  {plan.blockers.map((b, i) => (
                    <li key={i}>{b}</li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}

        {study.state === "plan_ready" && plan?.deposit_conflict && (
          <section className="mb-6">
            <DepositConflictNotice
              studyId={study.id}
              conflict={plan.deposit_conflict}
              onChanged={(updated) => setStudy(updated as ValidationStudy)}
            />
          </section>
        )}

        {study.state === "error" && (
          <section className="mb-6">
            <RetryNotice
              studyId={study.id}
              failureReason={study.failure_reason}
              reapAfter={study.evidence?.fetch_reap_after as string | undefined}
              dataDeleted={!!study.evidence?.fetch_reaped}
              onChanged={(updated) => setStudy(updated as ValidationStudy)}
            />
          </section>
        )}

        {study.state === "samples_mismatch" && (
          <section className="mb-6">
            <SamplesMismatchNotice
              studyId={study.id}
              failureReason={study.failure_reason}
              onChanged={(updated) => setStudy(updated as ValidationStudy)}
            />
          </section>
        )}

        {study.state === "plan_ready" && (
          <section className="mb-6">
            <PipelineInstallNotice
              pipelineKey={plan?.pipeline_key}
              pipelineVersion={plan?.pipeline_version}
              registryName={plan?.pipeline_registry_name}
              installed={plan?.pipeline_installed}
              onInstalled={refresh}
            />
          </section>
        )}

        {study.state === "plan_ready" && (
          <section className="mb-6">
            <Level3Gate
              studyId={study.id}
              design={plan?.differential_design}
              claim={plan?.finding_claim}
              supportedFindingKinds={plan?.supported_finding_kinds}
              onChanged={(updated) => setStudy(updated as ValidationStudy)}
            />
          </section>
        )}

        {study.evidence?.level3_result && (
          <section className="mb-6">
            <Level3ResultPanel
              result={study.evidence.level3_result}
              contrast={plan?.differential_design?.contrasts?.[0]?.name ?? undefined}
            />
          </section>
        )}

        <section className="mb-6">
          <ValidationStudyActions
            study={{
              id: study.id,
              state: study.state,
              evidence: { awaiting_refetch_approval: !!study.evidence?.awaiting_refetch_approval },
              plan: { deposit_conflict: plan?.deposit_conflict ?? null },
            }}
            onChanged={(updated) => setStudy(updated as ValidationStudy)}
            suggestedClassification={study.evidence?.classification_result?.classification}
          />
        </section>

        <section>
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500">Evidence</h2>
          <ValidationEvidenceTable evidence={study.evidence} />
        </section>
      </main>
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-gray-500">{label}</dt>
      <dd className="text-gray-800">{children}</dd>
    </div>
  );
}
