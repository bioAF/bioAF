"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter, useParams } from "next/navigation";
import { Breadcrumb } from "@/components/layout/Breadcrumb";
import Link from "next/link";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ValidationStudyOutcome } from "@/components/validation/ValidationStudyOutcome";
import { ValidationStudyActions, type StudyActivity } from "@/components/validation/ValidationStudyActions";
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
import {
  AiDecisionList,
  type AiDecision,
} from "@/components/validation/AiDecisionList";
import {
  ValidationIssuesSection,
  type ValidationIssue,
} from "@/components/validation/ValidationIssuesSection";
import { CapabilityChecklist, type Capabilities } from "@/components/validation/CapabilityChecklist";
import { CompletionSummary, type Completion } from "@/components/validation/CompletionSummary";
import { SupplementInventory, type Supplement } from "@/components/validation/SupplementInventory";
import {
  PrecomputeChecksPanel,
  type PrecomputeChecks,
  type SpeciesOverride,
} from "@/components/validation/PrecomputeChecksPanel";
import { DepositPanel, type DepositEvidence, type DepositSelection } from "@/components/validation/DepositPanel";
import { CodeSection, type CodeEvidence } from "@/components/validation/CodeSection";
import { ExpectedVsObserved, type ExpectedEvidence } from "@/components/validation/ExpectedVsObserved";
import { PROVISIONAL_NOTE, type ReportSummary } from "@/lib/validationReport";
import { ClaimSelection } from "@/components/validation/ClaimSelection";
import { ResourceInventory } from "@/components/validation/ResourceInventory";
import { ValidationScorecard } from "@/components/validation/ValidationScorecard";
import { TechnicalDetails } from "@/components/validation/TechnicalDetails";
import { InventoryRetryNotice } from "@/components/validation/InventoryRetryNotice";
import { RecoveryNotice } from "@/components/validation/RecoveryNotice";
import { ReportSection, useDisclosureLinks } from "@/components/validation/ReportSection";
import { ReportFindings } from "@/components/validation/ReportFindings";
import { ReportDataAndCode } from "@/components/validation/ReportDataAndCode";
import { usePermissions } from "@/hooks/usePermissions";
import { Card } from "@/components/ui/Card";
import type { ReportSections } from "@/lib/validationReport";
import { NOT_SET } from "@/lib/placeholders";

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
  // What the model decided about each of the paper's claims, and how sure it was. Rendered at the
  // C1 gate in both autonomy modes: the person approving the run is authorising these decisions.
  ai_decisions?: AiDecision[] | null;
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
  // plan_7 step 14c: steps that hit an error which may affect this validation. Study-scoped, so it
  // carries failures that happened before there was a plan to hang them off. Empty is the normal
  // case and renders nothing.
  issues?: ValidationIssue[] | null;
  // change_7.3 section 11: the report as one projection of the evidence. The panels below render from
  // it, and the JSON and markdown exports carry the same statements.
  report_summary?: ReportSummary | null;
  // The route chosen at the button, and what bioAF is doing on the study right now.
  intended_route?: string | null;
  activity?: StudyActivity | null;
}

// The evidence keys the plan_7 panels read. The bundle carries more than this; these are the ones
// with a surface. Kept beside the page rather than widened into `Evidence`, which belongs to the
// computed-vs-claimed table.
type Plan7Evidence = DepositEvidence &
  CodeEvidence &
  ExpectedEvidence & {
    capabilities?: Capabilities | null;
    precompute_checks?: PrecomputeChecks | null;
    species_override?: SpeciesOverride | null;
    // Set when the route chosen at the button turned out to be impossible once the paper was read.
    // change_7.2 section 1: `action` is which of the six typed policy answers refused it, so the
    // notice can tell a missing adapter from a missing input from a missing authorization.
    route_blocked?: { chosen?: string | null; reason?: string | null; action?: string | null } | null;
    // change_7.2 sections 2 and 3: the two holds a person resolves. Without a surface they are
    // exactly the indefinite hold this change exists to remove.
    awaiting_choice?: { reason?: string | null; action?: string | null } | null;
    awaiting_adoption?: { operation?: string | null; action?: string | null } | null;
    // change_7.1 section 2: the article's own attachments, named at read time and resolved to real
    // files once something goes and gets them.
    supplements?: Supplement[] | null;
    // change_7.1 section 7: why the assessment ended where it did, with every limitation that
    // applied rather than one label standing in for all of them.
    completion?: Completion | null;
  };

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
  const { canAccess } = usePermissions();
  // plan_8_2 section 4.2: a link to a finding or claim opens the disclosures around it.
  useDisclosureLinks(!!study);

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

  // While bioAF moves the study on by itself, poll so the stage and evidence stay current. The server
  // decides: a fixed list of states here had drifted from the driver's, so study 37's page loaded once
  // in `requested` and still offered "Read paper" after the driver had begun reading it.
  useEffect(() => {
    if (!study?.activity?.advancing) return;
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
  const plan7 = (study.evidence ?? {}) as Plan7Evidence;
  const summary = study.report_summary ?? null;
  // plan_8_1 section 1.4: the projection's blockers when the report carries them, else the plan's own.
  const allBlockerRows: { text: string; kind?: string | null; withheld?: string }[] =
    summary?.blockers && summary.blockers.length > 0
      ? summary.blockers
      : (plan?.blockers ?? []).map((text) => ({ text }));
  // plan_8_2 section 4.1: requirements that do not apply to a paper outside bioAF's methods are one collapsed
  // detail, never a list of blockers.
  const notApplying = allBlockerRows.filter((b) => b.kind === "not_applicable");
  const blockerRows = allBlockerRows.filter((b) => b.kind !== "not_applicable");
  const fallbackTitle = `Study #${study.id}`;
  const displayTitle = study.title || fallbackTitle;
  const sections = summary?.sections ?? null;

  // The study's outcome (headline, classification, its summary sentences), at the top of the scorecard.
  const outcome = (
    <div className="space-y-2">
      {summary?.scorecard && (
        <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Outcome</h3>
      )}
      <ValidationStudyOutcome
        state={study.state}
        confidence={study.confidence}
        classification={study.classification}
        failureReason={study.failure_reason}
        summary={summary}
      />
      {/* plan_8_1 section 1.4: a classification a failed read produced is not about the paper. */}
      {summary?.read_failure?.classification_note && (
        <p className="text-sm text-gray-700" data-testid="classification-from-failed-read">
          {summary.read_failure.classification_note}
        </p>
      )}
    </div>
  );

  // plan_8_2 section 4.2: the controls a study waits on sit together under the scorecard while their state
  // holds. Each is the same control as before; none is removed.
  const canPick = study.state === "acquiring_processed" && !plan7.deposit_selection;
  const hasDeposit = !!(plan7.deposit_inventory || plan7.deposit_selection || plan7.deposit_failed);
  const speciesBlocking = !!plan7.precompute_checks?.species_matches?.blocking && !plan7.species_override;
  const approver = canAccess("lit_validation", "approve");
  const requester = canAccess("lit_validation", "request");
  const actionsVisible =
    study.state === "requested" ||
    (approver &&
      [
        "plan_ready",
        "acquiring_data",
        "acquiring_processed",
        "inspecting_deposit",
        "classified",
        "error",
        "comparing",
      ].includes(study.state));
  const decisionsVisible =
    (requester && (!!summary?.scorecard?.inventory_retry || !!summary?.recovery?.available)) ||
    ["error", "samples_mismatch", "plan_ready"].includes(study.state) ||
    (canPick && hasDeposit) ||
    speciesBlocking ||
    actionsVisible;
  const depositPanel = hasDeposit ? (
    <DepositPanel
      evidence={plan7}
      // A person picks only while the study is waiting for one. After acquisition the
      // choice is made and re-offering it would suggest it could still be changed.
      canPick={canPick}
      onPick={async (selection: DepositSelection) => {
        await api.post(`/api/validation-studies/${study.id}/deposit-selection`, selection);
        await refresh();
      }}
    />
  ) : null;
  const precomputePanel = plan7.precompute_checks ? (
    <PrecomputeChecksPanel
      checks={plan7.precompute_checks}
      override={plan7.species_override ?? null}
      onOverride={async (reason) => {
        await api.post(`/api/validation-studies/${study.id}/override-species`, { reason });
        await refresh();
      }}
    />
  ) : null;
  const studyActions = (
    <ValidationStudyActions
      study={{
        id: study.id,
        state: study.state,
        evidence: {
          awaiting_refetch_approval: !!study.evidence?.awaiting_refetch_approval,
          // Threading these through is what makes the gate capability-aware. Passing only
          // `awaiting_refetch_approval` left `capabilities` null on every study, so plan_7
          // step 15's availability notes could never render and the modal offered three
          // equal-looking routes, which is the defect step 13 exists to remove.
          // Narrowed to the two answers the route chooser reads. The checklist's fuller
          // `Capabilities` shape carries `code_sources`, which the chooser has no use for.
          capabilities: plan7.capabilities
            ? {
                preprocessed_data: plan7.capabilities.preprocessed_data,
                raw_data: plan7.capabilities.raw_data,
              }
            : null,
          route_blocked: plan7.route_blocked ?? null,
          awaiting_choice: plan7.awaiting_choice ?? null,
          awaiting_adoption: plan7.awaiting_adoption ?? null,
        },
        plan: { deposit_conflict: plan?.deposit_conflict ?? null },
        resume: summary?.resume ?? null,
        intended_route: study.intended_route ?? null,
        activity: study.activity ?? null,
      }}
      onChanged={(updated) => setStudy(updated as ValidationStudy)}
      suggestedClassification={study.evidence?.classification_result?.classification}
    />
  );

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

        {/* plan_8_2 section 4.2 (approved 2026-09-14): the scorecard, with the outcome in it, leads; a strip of
            the decisions the study is waiting on follows; then four sections, each with its summary. */}
        {summary?.scorecard ? (
          <div className="mb-4">
            <ValidationScorecard scorecard={summary.scorecard} variant="summary" header={outcome} />
          </div>
        ) : (
          <section className="mb-6">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500">Outcome</h2>
            {outcome}
          </section>
        )}

        {decisionsVisible && (
          <div data-testid="needs-a-decision" className="mb-4">
            <Card title="Needs a decision" padding="sm">
              <div className="space-y-3">
                {summary?.scorecard?.inventory_retry && (
                  <InventoryRetryNotice studyId={study.id} onChanged={(updated) => setStudy(updated as ValidationStudy)} />
                )}
                {/* plan_8_2 section 2.1: re-evaluation under bioAF's current rules, on request only. */}
                {summary?.recovery?.available && (
                  <RecoveryNotice
                    studyId={study.id}
                    onChanged={(updated) => setStudy(updated as ValidationStudy)}
                    restate={summary?.recovery?.restate}
                    affectedCount={summary?.recovery?.affected_count}
                  />
                )}
                {study.state === "error" && (
                  <RetryNotice
                    studyId={study.id}
                    failureReason={study.failure_reason}
                    readFailed={!!summary?.read_failure}
                    reapAfter={study.evidence?.fetch_reap_after as string | undefined}
                    dataDeleted={!!study.evidence?.fetch_reaped}
                    onChanged={(updated) => setStudy(updated as ValidationStudy)}
                  />
                )}
                {study.state === "samples_mismatch" && (
                  <SamplesMismatchNotice
                    studyId={study.id}
                    failureReason={study.failure_reason}
                    onChanged={(updated) => setStudy(updated as ValidationStudy)}
                  />
                )}
                {study.state === "plan_ready" && plan?.deposit_conflict && (
                  <DepositConflictNotice
                    studyId={study.id}
                    conflict={plan.deposit_conflict}
                    onChanged={(updated) => setStudy(updated as ValidationStudy)}
                  />
                )}
                {study.state === "plan_ready" && (
                  <PipelineInstallNotice
                    pipelineKey={plan?.pipeline_key}
                    pipelineVersion={plan?.pipeline_version}
                    registryName={plan?.pipeline_registry_name}
                    installed={plan?.pipeline_installed}
                    onInstalled={refresh}
                  />
                )}
                {study.state === "plan_ready" && (
                  <Level3Gate
                    studyId={study.id}
                    design={plan?.differential_design}
                    claim={plan?.finding_claim}
                    supportedFindingKinds={plan?.supported_finding_kinds}
                    onChanged={(updated) => setStudy(updated as ValidationStudy)}
                  />
                )}
                {canPick && depositPanel}
                {speciesBlocking && precomputePanel}
                {studyActions}
              </div>
            </Card>
          </div>
        )}
        {!decisionsVisible && studyActions}

        <ReportSection
          id="findings"
          title="Findings"
          summary={sections?.findings.summary}
          counts={sections?.findings.counts ?? []}
          defaultOpen
        >
          {summary ? (
            <ReportFindings
              summary={summary}
              studyId={study.id}
              onChanged={(updated) => setStudy(updated as ValidationStudy)}
            />
          ) : (
            <p className="text-sm text-gray-600">The findings are established when the paper is read.</p>
          )}
        </ReportSection>

        <ReportSection
          id="data"
          title="Data and code"
          summary={sections?.data.summary}
          counts={sections?.data.counts ?? []}
        >
          {summary && (
            <ReportDataAndCode summary={summary}>
              {plan7.capabilities && (
                <details className="text-sm">
                  <summary className="cursor-pointer text-xs font-medium text-gray-700">What this paper has</summary>
                  <div className="mt-2">
                    <CapabilityChecklist capabilities={plan7.capabilities} rows={summary?.capability_rows} />
                  </div>
                </details>
              )}
              {((plan7.supplements && plan7.supplements.length > 0) || (summary?.artifacts?.length ?? 0) > 0) && (
                <div>
                  <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
                    What the paper attached
                  </h3>
                  <SupplementInventory supplements={plan7.supplements} summary={summary} />
                </div>
              )}
              {!canPick && hasDeposit && (
                <div>
                  <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">Deposited data</h3>
                  {depositPanel}
                </div>
              )}
            </ReportDataAndCode>
          )}
        </ReportSection>

        <ReportSection
          id="checks"
          title="Checks performed"
          summary={sections?.checks.summary}
          counts={sections?.checks.counts ?? []}
        >
          {(sections?.checks.rows.length ?? 0) > 0 && <ChecksTable rows={sections?.checks.rows ?? []} />}
          {(summary?.selection || (summary?.experiments?.length ?? 0) > 0) && (
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">What this run checks</h3>
              <ClaimSelection summary={summary} parts={["selection", "experiments"]} />
            </div>
          )}
          {study.evidence?.classification_result && (
            <ValidationVerdictPanel
              result={study.evidence.classification_result}
              level3Skipped={study.evidence.level3_skipped}
              level3Failed={study.evidence.level3_failed}
            />
          )}
          {study.evidence?.level3_result && (
            <Level3ResultPanel
              result={study.evidence.level3_result}
              contrast={plan?.differential_design?.contrasts?.[0]?.name ?? undefined}
            />
          )}
          {plan7.completion && (
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                What could and could not be established
              </h3>
              <CompletionSummary completion={plan7.completion} summary={summary} />
              {/* change_7.3 section 10 item 13: whether reconciliation ran and on what, and whether the
                  assessment's statements were checked against each other at all. */}
              {summary?.reconciliation?.label && (
                <p className="mt-2 text-xs text-gray-600">{summary.reconciliation.label}.</p>
              )}
              {summary?.consistency?.label && <p className="text-xs text-gray-600">{summary.consistency.label}.</p>}
            </div>
          )}
          {plan7.precompute_checks && !speciesBlocking && (
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                Checks before spending compute
              </h3>
              {precomputePanel}
            </div>
          )}
          {plan && (
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Reproduction plan</h3>
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
              {plan.ai_decisions && plan.ai_decisions.length > 0 && (
                <AiDecisionList decisions={plan.ai_decisions} testedCount={summary?.claim_counts?.tested ?? 0} />
              )}
              {(blockerRows.length > 0 || notApplying.length > 0) && (
                <div className="mt-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-amber-700">Blockers</p>
                  <ul className="mt-1 list-inside list-disc text-sm text-gray-700">
                    {/* plan_8_1 section 1.4: the projection's blockers, so a failed read's statements
                        about the paper stay withheld; the sentence withheld sits in a collapsed detail. */}
                    {blockerRows.map((b, i) =>
                      b.withheld ? (
                        <li key={i} data-testid="blocker-withheld">
                          {b.text}
                          <TechnicalDetails detail={{ withheld: b.withheld }} summary="Withheld statement" />
                        </li>
                      ) : (
                        <li key={i}>{b.text}</li>
                      ),
                    )}
                    {notApplying.length > 0 && (
                      <li data-testid="blockers-not-applying" className="text-gray-600">
                        {notApplying.length} {notApplying.length === 1 ? "requirement that does" : "requirements that do"}{" "}
                        not apply: {notApplying[0].text.replace(/^Does not apply: /, "")}
                        <TechnicalDetails
                          detail={{ requirements: notApplying.map((b) => b.withheld) }}
                          summary="Requirements that do not apply"
                        />
                      </li>
                    )}
                  </ul>
                  {/* change_7.3 section 6: a blocker is a reading of the prose until inspected evidence
                      settles it, and it must not read as an established fact about the paper. */}
                  {summary?.blockers?.some((b) => b.provisional) && (
                    <p className="mt-1 text-xs text-gray-500" data-testid="blockers-provisional">
                      Provisional: {PROVISIONAL_NOTE}.
                    </p>
                  )}
                </div>
              )}
            </div>
          )}
          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Evidence</h3>
            <ValidationEvidenceTable evidence={study.evidence} attemptStatus={summary?.attempt?.status ?? null} />
          </div>
          {/* plan_7 step 19 part 2's sibling: what the deposit and its metadata led us to expect,
              against what the acquired data turned out to be. */}
          {(plan7.deposit_inspection ||
            plan7.precompute_checks ||
            (study.state === "classified" && summary && !summary.comparisons.performed)) && (
            <div>
              {/* change_7.3 section 10 item 9: a heading that promises a comparison stays for runs that
                  compared something. Where nothing was compared, it says so and why. */}
              {summary && !summary.comparisons.performed ? (
                <>
                  <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
                    {summary.comparisons.label}
                  </h3>
                  <p className="mb-2 text-xs text-gray-600">
                    {summary.comparisons.reason
                      ? `${summary.comparisons.reason.charAt(0).toUpperCase()}${summary.comparisons.reason.slice(1)}.`
                      : null}
                  </p>
                </>
              ) : (
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                  What we expected, and what we saw
                </h3>
              )}
              <ExpectedVsObserved evidence={plan7} />
            </div>
          )}
          {(plan7.code_resolution || plan7.code_execution) && (
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                The authors&apos; code
              </h3>
              <CodeSection evidence={plan7} />
            </div>
          )}
        </ReportSection>

        <ReportSection
          id="diagnostics"
          title="Run diagnostics"
          summary={sections?.diagnostics.summary}
          counts={sections?.diagnostics.counts ?? []}
        >
          {(sections?.diagnostics.checks.length ?? 0) > 0 && (
            <CheckRecordsTable rows={sections?.diagnostics.checks ?? []} />
          )}
          {(summary?.selection_history?.length ?? 0) > 0 && (
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Selection history</h3>
              <ClaimSelection summary={summary} parts={["history"]} />
            </div>
          )}
          <ValidationIssuesSection issues={study.issues ?? []} />
        </ReportSection>
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

// plan_8_2 section 4.2: each check bioAF has, how many claims it applies to, and what it found.
function ChecksTable({ rows }: { rows: ReportSections["checks"]["rows"] }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm" data-testid="checks-table">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-gray-500">
            <th scope="col" className="py-1 pr-4 font-medium">Check</th>
            <th scope="col" className="py-1 pr-4 font-medium">Claims</th>
            <th scope="col" className="py-1 pr-4 font-medium">Available</th>
            <th scope="col" className="py-1 pr-4 font-medium">Unresolved</th>
            <th scope="col" className="py-1 pr-4 font-medium">Unavailable</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className="border-t border-gray-100">
              <td className="py-1.5 pr-4 text-gray-800">{row.label}</td>
              <td className="py-1.5 pr-4 tabular-nums">{row.claims}</td>
              <td className="py-1.5 pr-4 tabular-nums">{row.available}</td>
              <td className="py-1.5 pr-4 tabular-nums">{row.unresolved}</td>
              <td className="py-1.5 pr-4 tabular-nums">{row.unavailable}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// plan_8_2 section 4.2: the check records, with their retries, revisions and terminal reasons.
function CheckRecordsTable({ rows }: { rows: ReportSections["diagnostics"]["checks"] }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm" data-testid="check-records-table">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-gray-500">
            <th scope="col" className="py-1 pr-4 font-medium">Check record</th>
            <th scope="col" className="py-1 pr-4 font-medium">State</th>
            <th scope="col" className="py-1 pr-4 font-medium">Revision</th>
            <th scope="col" className="py-1 pr-4 font-medium">Retries</th>
            <th scope="col" className="py-1 pr-4 font-medium">Ended because</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.check_id ?? i} className="border-t border-gray-100 align-top">
              <td className="py-1.5 pr-4 font-mono text-xs">{row.check_id}</td>
              <td className="py-1.5 pr-4">{row.activity ?? row.state}</td>
              <td className="py-1.5 pr-4 tabular-nums">{row.revision}</td>
              <td className="py-1.5 pr-4 tabular-nums">{row.retry_count}</td>
              <td className="py-1.5 pr-4 text-xs text-gray-600">{row.terminal_reason ?? NOT_SET}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
