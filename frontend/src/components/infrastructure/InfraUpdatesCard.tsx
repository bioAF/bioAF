"use client";

import { useState } from "react";
import {
  infrastructure,
  type CheckUpdatesResult,
} from "@/lib/infrastructure";

type Phase =
  | "idle"
  | "checking"
  | "up_to_date"
  | "applying"
  | "needs_approval"
  | "error";

interface Props {
  // Called once an apply has been kicked off so the parent can refresh status
  // and surface the in-progress banner / progress.
  onApplyStarted?: () => void;
}

// "Check for Infrastructure Updates": re-plans deployed modules. Additive
// changes apply automatically; a delete or replace of stored data is held and
// shown for explicit approval.
export function InfraUpdatesCard({ onApplyStarted }: Props) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState<CheckUpdatesResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function check() {
    setPhase("checking");
    setError(null);
    setResult(null);
    try {
      const r = await infrastructure.checkUpdates();
      setResult(r);
      if (!r.has_changes) {
        setPhase("up_to_date");
      } else if (r.requires_approval) {
        setPhase("needs_approval");
      } else {
        setPhase("applying");
        onApplyStarted?.();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not check for updates.");
      setPhase("error");
    }
  }

  async function approveAndApply() {
    if (!result) return;
    setPhase("checking");
    setError(null);
    try {
      await infrastructure.applyUpdates(result.modules_with_changes);
      setPhase("applying");
      onApplyStarted?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not apply updates.");
      setPhase("error");
    }
  }

  return (
    <div className="bg-white rounded-lg shadow border border-gray-200 p-4 mb-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">
            Infrastructure updates
          </h3>
          <p className="text-xs text-gray-600 mt-1 max-w-2xl">
            Re-check deployed infrastructure against the latest configuration,
            for example to add a newly introduced storage bucket. Additive
            changes are applied automatically. Anything that would delete or
            replace stored data is held for your approval.
          </p>
        </div>
        <button
          onClick={check}
          disabled={phase === "checking"}
          className="ml-4 shrink-0 px-3 py-1.5 text-sm rounded bg-bioaf-600 text-white hover:bg-bioaf-700 disabled:opacity-50"
        >
          {phase === "checking" ? "Checking..." : "Check for Infrastructure Updates"}
        </button>
      </div>

      {phase === "up_to_date" && (
        <div className="mt-3 text-sm text-green-700">
          Your infrastructure is up to date.
        </div>
      )}

      {phase === "applying" && (
        <div className="mt-3 text-sm text-bioaf-700">
          Applying updates in the background. Progress appears below.
        </div>
      )}

      {phase === "error" && (
        <div className="mt-3 text-sm text-red-700">{error}</div>
      )}

      {phase === "needs_approval" && result && (
        <div className="mt-3 border border-red-200 bg-red-50 rounded p-3">
          <p className="text-sm font-medium text-red-800">
            This update would destroy or replace stored data.
          </p>
          <p className="text-xs text-red-700 mt-1">
            Review the affected resources. Applying permanently changes them and
            can lose data.
          </p>
          <ul className="mt-2 text-xs text-red-900 list-disc pl-5 space-y-0.5">
            {result.destructive_resources.map((r) => (
              <li key={r.address}>
                <span className="font-mono uppercase">{r.action}</span>{" "}
                {r.description || r.address}
              </li>
            ))}
          </ul>
          <div className="flex gap-2 mt-3">
            <button
              onClick={() => setPhase("idle")}
              className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              onClick={approveAndApply}
              className="px-3 py-1.5 text-sm bg-red-600 text-white rounded hover:bg-red-700"
            >
              Apply anyway
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
