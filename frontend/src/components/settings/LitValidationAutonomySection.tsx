"use client";

/**
 * plan_6 step 5: how much of literature validation the model decides for itself.
 *
 * The obvious reading of "autonomous" is that it takes the human out of the loop, and that is not
 * what it does. A person still approves every study before it runs, in both modes, because that is
 * where the compute is authorised. What the setting governs is the scientific judgment inside a
 * plan: which sample a claim refers to, which contrast the paper tested, which build it used.
 */

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { literature } from "@/lib/literature";
import { logError } from "@/lib/errorReporting";

const MODES = [
  {
    value: "assisted",
    label: "Assisted (default)",
    blurb:
      "The model proposes, and anything it declines or is unsure of is surfaced on the study for a person to resolve.",
  },
  {
    value: "autonomous",
    label: "Autonomous",
    blurb:
      "The model chooses rather than defers, and records how confident it was. Its decisions are still shown on every study.",
  },
];

export function LitValidationAutonomySection() {
  const [value, setValue] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    literature
      .getLitValidationSettings()
      .then((s) => {
        if (!cancelled) setValue(s.autonomy);
      })
      .catch((e) => setError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, []);

  async function save() {
    if (!value) return;
    setError(null);
    setSaved(false);
    setSaving(true);
    try {
      const next = await literature.updateLitValidationSettings({ autonomy: value });
      setValue(next.autonomy);
      setSaved(true);
    } catch (e) {
      logError("saving the literature validation autonomy setting", e);
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (value === null && !error) return null;

  const active = MODES.find((m) => m.value === value);

  return (
    <div className="bg-white border border-gray-200 rounded p-4 space-y-2">
      <h3 className="font-semibold text-sm">Literature Validation Autonomy</h3>
      <p className="text-xs text-gray-500">
        How much of a validation study the model decides for itself. A person still has to approve
        every study before it runs, in both modes, because that is where the compute is authorised.
      </p>
      <div className="flex items-center gap-2">
        <label className="sr-only" htmlFor="lit-validation-autonomy">
          Autonomy
        </label>
        <select
          id="lit-validation-autonomy"
          value={value ?? "assisted"}
          onChange={(e) => {
            setValue(e.target.value);
            setSaved(false);
          }}
          className="border border-gray-300 rounded px-3 py-1.5 text-sm"
        >
          {MODES.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>
        <Button size="sm" onClick={save} disabled={saving}>
          {saving ? "Saving..." : "Save"}
        </Button>
        {saved && <span className="text-xs text-green-700">Saved</span>}
      </div>
      {active && <p className="text-xs text-gray-500">{active.blurb}</p>}
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  );
}
