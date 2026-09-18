"use client";

import { useId, useState } from "react";

import { Button } from "@/components/ui/Button";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";

/**
 * plan_8_2 section 3.1: record how a table reads when bioAF's own evidence does not establish it.
 *
 * A headerless table, or one whose link to its contrast rests on its filename alone, is not deficient; the
 * check waits for what is missing. A person who knows (from a README, a legend, the authors' code) records
 * that the table reports the contrast, which column holds each statistic, and the effect scale and ratio
 * orientation, with the evidence. Only that contrast's checks are re-evaluated. Column numbers are shown
 * counting from 1. The labels are pending the owner's sign-off.
 */
type Role = "id" | "lfc" | "pvalue" | "padj";

const ROLES: { role: Role; label: string }[] = [
  { role: "id", label: "Identifier column" },
  { role: "lfc", label: "Fold change column" },
  { role: "pvalue", label: "P value column" },
  { role: "padj", label: "Adjusted P value column" },
];

/**
 * plan_8_4 defect 1: the magnitude reading of a documented refinement of a published list.
 *
 * The paper writes "a log2 fold change >2". Read as the signed value that selects one set of genes;
 * read as the magnitude it selects another, and only evidence says which the authors meant. The
 * service and the endpoint have taken this since plan_8_3 and the form had no field for it, so the
 * one thing that settles Groff's 88-gene claim could not be recorded through the application.
 *
 * The field appears only where this is what is open: a comparison unresolved for some other reason
 * has no reading to state, and a reading already settled is stated rather than asked again.
 */
export interface FilterSemantics {
  unresolved: boolean;
  reason: string | null;
  statement: string | null;
  magnitude: boolean | null;
  resolved_by: string | null;
}

interface Props {
  studyId: number;
  table: string;
  contrast: string;
  columnsCount?: number | null;
  candidateRoles?: Partial<Record<Role, number[]>> | null;
  filterSemantics?: FilterSemantics | null;
  onChanged: (updated: unknown) => void;
}

export function TableConfirmation({
  studyId,
  table,
  contrast,
  columnsCount,
  candidateRoles,
  filterSemantics,
  onChanged,
}: Props) {
  const { canAccess } = usePermissions();
  const id = useId();
  const [open, setOpen] = useState(false);
  const [reports, setReports] = useState(false);
  const [columns, setColumns] = useState<Record<Role, string>>({ id: "", lfc: "", pvalue: "", padj: "" });
  const [scale, setScale] = useState("");
  const [orientation, setOrientation] = useState("");
  const [magnitude, setMagnitude] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  if (!canAccess("lit_validation", "request")) return null;

  async function record() {
    setBusy(true);
    setRefusal(null);
    setFailed(false);
    const chosen = Object.fromEntries(
      ROLES.filter(({ role }) => columns[role].trim()).map(({ role }) => [role, Number(columns[role]) - 1]),
    );
    try {
      onChanged(
        await api.post(`/api/validation-studies/${studyId}/table-confirmations`, {
          table,
          contrast,
          reports_contrast: reports,
          columns: Object.keys(chosen).length ? chosen : null,
          effect_scale: scale || null,
          orientation: orientation || null,
          // Sent only where a reading was stated: an absent field settles nothing, and the server
          // refuses a filter confirmation that says anything but which of the two readings it is.
          ...(magnitude ? { filter_semantics: { magnitude: magnitude === "magnitude" } } : {}),
          note: note.trim(),
        }),
      );
      setOpen(false);
    } catch (e) {
      // A 422 or 409 is the server's plain sentence for the page.
      const status = (e as { status?: number }).status;
      if (e instanceof Error && (status === 422 || status === 409)) {
        setRefusal(e.message);
      } else {
        logError("recording how a validation study's table reads", e);
        setFailed(true);
      }
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div className="mt-1">
        <Button variant="secondary" onClick={() => setOpen(true)}>
          Record how this table reads
        </Button>
      </div>
    );
  }

  return (
    <div className="mt-2 space-y-2 rounded border border-gray-200 bg-gray-50 p-3 text-xs" data-testid="table-confirmation">
      <p className="text-gray-700">
        {table}
        {columnsCount ? ` (${columnsCount} columns)` : ""}
      </p>
      <label className="flex items-center gap-2 text-gray-700">
        <input type="checkbox" checked={reports} onChange={(event) => setReports(event.target.checked)} />
        This table reports {contrast}
      </label>
      <div className="flex flex-wrap gap-3">
        {ROLES.map(({ role, label }) => {
          const hint = candidateRoles?.[role];
          return (
            <div key={role} className="text-gray-600">
              <label htmlFor={`${id}-${role}`} className="block">
                {label}
              </label>
              <input
                id={`${id}-${role}`}
                type="number"
                min={1}
                max={columnsCount ?? undefined}
                value={columns[role]}
                onChange={(event) => setColumns((prev) => ({ ...prev, [role]: event.target.value }))}
                className="mt-1 w-20 rounded border border-gray-300 px-2 py-1 text-sm"
              />
              {hint && hint.length > 0 && (
                <span className="block text-gray-500">could be columns {hint.map((i) => i + 1).join(", ")}</span>
              )}
            </div>
          );
        })}
      </div>
      <div className="flex flex-wrap gap-3">
        <div className="text-gray-600">
          <label htmlFor={`${id}-scale`} className="block">
            Effect scale
          </label>
          <select
            id={`${id}-scale`}
            value={scale}
            onChange={(event) => setScale(event.target.value)}
            className="mt-1 rounded border border-gray-300 px-2 py-1 text-sm"
          >
            <option value="">Not recorded</option>
            <option value="log2">log2</option>
            <option value="linear">Linear</option>
          </select>
        </div>
        <div className="text-gray-600">
          <label htmlFor={`${id}-orientation`} className="block">
            Ratio orientation
          </label>
          <select
            id={`${id}-orientation`}
            value={orientation}
            onChange={(event) => setOrientation(event.target.value)}
            className="mt-1 rounded border border-gray-300 px-2 py-1 text-sm"
          >
            <option value="">Not recorded</option>
            <option value="test_over_reference">Test over reference</option>
            <option value="reference_over_test">Reference over test</option>
          </select>
        </div>
      </div>
      {filterSemantics?.unresolved && (
        <div className="text-gray-600">
          {filterSemantics.statement && (
            <p className="text-gray-500">The paper says: &quot;{filterSemantics.statement.trim()}&quot;</p>
          )}
          <label htmlFor={`${id}-magnitude`} className="mt-1 block">
            What the fold-change cutoff is on
          </label>
          <select
            id={`${id}-magnitude`}
            value={magnitude}
            onChange={(event) => setMagnitude(event.target.value)}
            className="mt-1 rounded border border-gray-300 px-2 py-1 text-sm"
          >
            <option value="">Not recorded</option>
            <option value="magnitude">The size of the change, in either direction</option>
            <option value="signed">The signed value, in one direction</option>
          </select>
        </div>
      )}
      <label htmlFor={`${id}-note`} className="block text-gray-600">
        What establishes this
      </label>
      <textarea
        id={`${id}-note`}
        value={note}
        onChange={(event) => setNote(event.target.value)}
        rows={2}
        placeholder="A README, a legend, the authors' code"
        className="w-full rounded border border-gray-300 p-2 text-sm"
      />
      <div className="flex gap-2">
        <Button busy={busy} busyLabel="Recording..." disabled={!note.trim()} onClick={record}>
          Record
        </Button>
        <Button variant="secondary" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
      {refusal && <p className="text-sm text-gray-700">{refusal}</p>}
      {failed && (
        <p className="text-sm text-red-700">
          bioAF could not record that. The technical detail is in the application logs.
        </p>
      )}
    </div>
  );
}
