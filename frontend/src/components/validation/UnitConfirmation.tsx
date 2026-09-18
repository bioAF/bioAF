"use client";

import { useId, useState } from "react";

import { Button } from "@/components/ui/Button";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";
import type { ReportUnitConfirmation } from "@/lib/validationReport";

/**
 * plan_8_3 stage 5: record which biological unit each column came from, where the sources do not say.
 *
 * bioAF refuses a unit identity nothing the row cites states, and it is right to: a paper's donors,
 * clones and cultures cannot be read off a column's order or its trailing digits. What the refusal needs
 * is a way out. A person who knows records it here with the evidence it rests on, the result is reported
 * as assisted, and the same rules still apply to what a person states: a unit that is only what KIND of
 * unit it is, or that is the column's own name, is refused, and the server's sentence is what the page
 * shows. The labels are pending the owner's sign-off.
 */
export function UnitConfirmation({
  studyId,
  offer,
  onChanged,
}: {
  studyId: number;
  offer: ReportUnitConfirmation;
  onChanged: (updated: unknown) => void;
}) {
  const { canAccess } = usePermissions();
  const id = useId();
  const [units, setUnits] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  if (!canAccess("lit_validation", "request")) return null;

  const stated = Object.fromEntries(
    offer.unresolved.filter((column) => (units[column] ?? "").trim()).map((column) => [column, units[column].trim()]),
  );

  async function record() {
    setBusy(true);
    setRefusal(null);
    setFailed(false);
    try {
      onChanged(
        await api.post(`/api/validation-studies/${studyId}/unit-confirmations`, { units: stated, note: note.trim() }),
      );
    } catch (e) {
      // A 422 or 409 is the server's plain sentence for the page: it is the refusal, not a failure.
      const status = (e as { status?: number }).status;
      if (e instanceof Error && (status === 422 || status === 409)) {
        setRefusal(e.message);
      } else {
        logError("recording a validation study's biological units", e);
        setFailed(true);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-3 space-y-2 rounded border border-gray-200 bg-gray-50 p-3" data-testid="unit-confirmation">
      <p className="text-sm text-gray-700">
        Which biological unit each sample came from is not established
        {offer.matrix ? ` for ${offer.matrix}` : ""}. bioAF does not infer it from a column&apos;s order, its trailing
        digits or a repeating name pattern. A result that rests on what you record here is reported as assisted.
      </p>

      {offer.recorded && (
        <div
          className="rounded border border-gray-200 bg-white p-2 text-xs text-gray-600"
          data-testid="unit-confirmation-recorded"
        >
          <p className="font-medium text-gray-700">Recorded</p>
          <ul className="mt-1 list-disc pl-5">
            {Object.entries(offer.recorded.units).map(([column, unit]) => (
              <li key={column}>
                {column}: {unit}
              </li>
            ))}
          </ul>
          <p className="mt-1">
            {offer.recorded.confirmed_by}
            {offer.recorded.note ? `: ${offer.recorded.note}` : ""}
          </p>
        </div>
      )}

      {offer.unresolved.length > 0 && (
        <>
          <div className="flex flex-wrap gap-3">
            {offer.unresolved.map((column) => (
              <div key={column} className="text-xs text-gray-600">
                <label htmlFor={`${id}-${column}`} className="block">
                  {column}
                </label>
                <input
                  id={`${id}-${column}`}
                  type="text"
                  value={units[column] ?? ""}
                  onChange={(event) => setUnits((prev) => ({ ...prev, [column]: event.target.value }))}
                  placeholder="donor 3, clone Cl16"
                  className="mt-1 w-40 rounded border border-gray-300 px-2 py-1 text-sm"
                />
              </div>
            ))}
          </div>
          <label htmlFor={`${id}-note`} className="block text-xs text-gray-600">
            What establishes this
          </label>
          <textarea
            id={`${id}-note`}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            rows={2}
            placeholder="The methods, a figure legend, the authors' code"
            className="w-full rounded border border-gray-300 p-2 text-sm"
          />
          <Button
            busy={busy}
            busyLabel="Recording..."
            disabled={!note.trim() || Object.keys(stated).length === 0}
            onClick={record}
          >
            Record these units
          </Button>
        </>
      )}

      {refusal && <p className="text-sm text-gray-700">{refusal}</p>}
      {failed && (
        <p className="text-sm text-red-700">
          bioAF could not record that. The technical detail is in the application logs.
        </p>
      )}
    </div>
  );
}
