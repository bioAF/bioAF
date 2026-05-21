"use client";

import { useState } from "react";
import {
  infrastructure,
  type CheckUpdatesResult,
  type ResourceInfo,
} from "@/lib/infrastructure";

type Phase = "idle" | "checking" | "up_to_date" | "review" | "applying" | "error";

interface Props {
  // Called once an apply has been kicked off so the parent can refresh status
  // and surface the in-progress banner / progress.
  onApplyStarted?: () => void;
}

function ResourceLine({ r }: { r: ResourceInfo }) {
  return (
    <li>
      <span className="font-mono uppercase">{r.action}</span>{" "}
      {r.description || r.address}
    </li>
  );
}

// "Check for Infrastructure Updates": re-plans deployed modules (re-aligning
// naming to the live deployment first). Additive changes apply automatically;
// destructive changes (delete/replace) are shown but never applied by this
// flow, so existing data buckets are never destroyed.
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
      } else if (r.applying) {
        setPhase("applying");
        onApplyStarted?.();
      } else {
        setPhase("review");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not check for updates.");
      setPhase("error");
    }
  }

  async function applyAdditive() {
    if (!result) return;
    setPhase("checking");
    setError(null);
    try {
      await infrastructure.applyUpdates(result.modules_with_additive);
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
            for example to add a newly introduced storage bucket. New resources
            are added automatically. Changes that would delete or replace
            existing data are shown but never applied here.
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

      {result?.realigned && (
        <div className="mt-3 text-xs text-gray-500">
          Re-aligned naming to the live deployment
          {result.realigned.stack_uid ? ` (stack ${result.realigned.stack_uid})` : ""}.
        </div>
      )}

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

      {phase === "review" && result && (
        <div className="mt-3 space-y-3">
          {result.has_additive && (
            <div className="border border-green-200 bg-green-50 rounded p-3">
              <p className="text-sm font-medium text-green-800">
                New resources to add ({result.additive_resources.length})
              </p>
              <ul className="mt-2 text-xs text-green-900 list-disc pl-5 space-y-0.5">
                {result.additive_resources.map((r) => (
                  <ResourceLine key={r.address} r={r} />
                ))}
              </ul>
            </div>
          )}

          {result.has_destructive && (
            <div className="border border-red-200 bg-red-50 rounded p-3">
              <p className="text-sm font-medium text-red-800">
                Will NOT be applied: would destroy or replace existing resources
                ({result.destructive_resources.length})
              </p>
              <p className="text-xs text-red-700 mt-1">
                These are skipped to protect your data. Resolve them with a full
                deploy/teardown if you really intend to change them.
              </p>
              <ul className="mt-2 text-xs text-red-900 list-disc pl-5 space-y-0.5">
                {result.destructive_resources.map((r) => (
                  <li key={r.address}>
                    <span className="font-mono uppercase">{r.action}</span>{" "}
                    {r.description || r.address}
                    {r.stateful ? (
                      <span className="ml-1 font-semibold">(stored data)</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="flex gap-2">
            <button
              onClick={() => setPhase("idle")}
              className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
            >
              Cancel
            </button>
            {result.has_additive && (
              <button
                onClick={applyAdditive}
                className="px-3 py-1.5 text-sm bg-bioaf-600 text-white rounded hover:bg-bioaf-700"
              >
                Apply additive changes only
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
