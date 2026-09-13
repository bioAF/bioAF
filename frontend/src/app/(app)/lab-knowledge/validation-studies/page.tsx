"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Breadcrumb } from "@/components/layout/Breadcrumb";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ScorecardCompact } from "@/components/validation/ScorecardCompact";
import { LitValidationGate } from "@/components/validation/LitValidationGate";
import { ErrorState } from "@/components/shared/ErrorState";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { statusBadgeClass } from "@/lib/statusStyles";
import { getValidationStage } from "@/lib/validationStage";
import type { CompactScorecard } from "@/lib/validationReport";

interface ValidationStudySummary {
  id: number;
  state: string;
  // Server-resolved display title (paper.title -> DOI -> accession -> "Study #{id}").
  title?: string | null;
  classification?: string | null;
  confidence?: number | null;
  // change_7.3: whether reproduction was attempted, so a study that ran nothing does not read as one
  // that failed.
  attempt?: "attempted" | "not_attempted" | null;
  paper_id?: number | null;
  source_doi?: string | null;
  source_accession?: string | null;
  experiment_id?: number | null;
  created_at?: string | null;
  // plan_8 section 7: the compact Validation Scorecard, from the same scorecard the report renders.
  scorecard?: CompactScorecard | null;
}

function formatDate(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString();
}

/**
 * The validation studies list: the org's reproduction attempts, each linking to its F1 detail page.
 * Studies are created from a paper in the Literature surface (the Validate action); this is the surface
 * to find and reopen them.
 */
export default function ValidationStudiesListPage() {
  const [studies, setStudies] = useState<ValidationStudySummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setStudies(null);
    try {
      const data = await api.get<ValidationStudySummary[]>("/api/validation-studies");
      setStudies(data);
    } catch (e) {
      logError("loading validation studies", e);
      setError(loadFailureMessage("Validation studies"));
    }
  }, []);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      <Breadcrumb />
      <main className="flex-1 overflow-y-auto p-6">
        <LitValidationGate>
          <h1 className="mb-1 text-2xl font-bold">Literature Validation</h1>
          <p data-testid="page-description" className="mb-6 text-sm text-gray-500">
            Reproduction attempts against papers. Start one from a paper in the Literature library.
          </p>

          {error ? (
            <ErrorState
              message={error}
              onRetry={load}
            />
          ) : studies === null ? (
            <div className="flex justify-center py-16">
              <LoadingSpinner size="lg" />
            </div>
          ) : studies.length === 0 ? (
            <div className="rounded border border-dashed border-gray-300 p-8 text-center text-sm text-gray-500">
              No validation studies yet. Open a paper in the Literature library and choose{" "}
              <span className="font-medium">Validate reproduction</span> to start one.
            </div>
          ) : (
            <div className="overflow-x-auto rounded-lg bg-white shadow">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Study</th>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Source</th>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Requested</th>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">Status</th>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">
                      Validation Scorecard
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {studies.map((s) => (
                    <tr key={s.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 text-sm">
                        <Link
                          href={`/lab-knowledge/validation-studies/${s.id}`}
                          className="font-medium text-bioaf-700 hover:underline"
                        >
                          {s.title || `Study #${s.id}`}
                        </Link>
                        {s.title && s.title !== `Study #${s.id}` && (
                          <span className="ml-2 text-xs text-gray-500">#{s.id}</span>
                        )}
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-gray-600">
                        {s.source_doi || s.source_accession || (s.paper_id ? `paper ${s.paper_id}` : "-")}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-500">{formatDate(s.created_at)}</td>
                      {/* plan_8 section 7: lifecycle state and the scorecard are different facts, so each
                          has its own column. */}
                      <td className="px-4 py-3 text-sm">
                        <span
                          title={getValidationStage(s.state).description}
                          className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${statusBadgeClass(
                            "validationStage",
                            getValidationStage(s.state).kind,
                          )}`}
                        >
                          {getValidationStage(s.state).label}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <ScorecardCompact scorecard={s.scorecard} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </LitValidationGate>
      </main>
    </>
  );
}
