"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter, useParams } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
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
import { Level3ResultPanel } from "@/components/validation/Level3ResultPanel";
import { ProvenanceExportMenu } from "@/components/shared/ProvenanceExportMenu";
import { isAuthenticated } from "@/lib/auth";
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
}

interface ValidationStudy {
  id: number;
  state: string;
  classification?: string | null;
  confidence?: number | null;
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

  const refresh = useCallback(async () => {
    try {
      const data = await api.get<ValidationStudy>(`/api/validation-studies/${id}`);
      setStudy(data);
    } catch {
      setStudy((prev) => prev ?? null);
    }
  }, [id]);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
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

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (!study) {
    return (
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex flex-1 flex-col overflow-hidden">
          <Header />
          <main className="flex flex-1 items-center justify-center">
            <p className="text-gray-500">Validation study not found</p>
          </main>
        </div>
      </div>
    );
  }

  const plan = study.plan;

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="mb-6 flex flex-wrap items-center gap-4">
            <button onClick={() => router.back()} className="text-gray-500 hover:text-gray-700">
              ← Back
            </button>
            <h1 className="text-2xl font-bold">Validation Study #{study.id}</h1>
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
              <ValidationVerdictPanel result={study.evidence.classification_result} />
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
                  <p className="text-xs font-semibold uppercase tracking-wide text-amber-600">Blockers</p>
                  <ul className="mt-1 list-inside list-disc text-sm text-gray-700">
                    {plan.blockers.map((b, i) => (
                      <li key={i}>{b}</li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
          )}

          {study.state === "plan_ready" && (
            <section className="mb-6">
              <Level3Gate
                studyId={study.id}
                design={plan?.differential_design}
                claim={plan?.finding_claim}
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
              study={{ id: study.id, state: study.state }}
              onChanged={(updated) => setStudy(updated as ValidationStudy)}
              suggestedClassification={study.evidence?.classification_result?.classification}
            />
          </section>

          <section>
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500">Evidence</h2>
            <ValidationEvidenceTable evidence={study.evidence} />
          </section>
        </main>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-gray-400">{label}</dt>
      <dd className="text-gray-800">{children}</dd>
    </div>
  );
}
