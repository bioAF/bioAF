"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { logError } from "@/lib/errorReporting";
import { Button } from "@/components/ui/Button";

export interface PlotScanHealth {
  status: "ok" | "missing" | "no_access" | "not_confirmed" | "temporary";
  last_success_at?: string | null;
  last_attempt_at?: string | null;
  next_attempt_at?: string | null;
}

function minutesUntil(iso: string | null | undefined): number {
  if (!iso) return 0;
  return Math.max(1, Math.round((new Date(iso).getTime() - Date.now()) / 60_000));
}

function formatWhen(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** Cause lines signed off in mockup M13. */
function cause(health: PlotScanHealth): string {
  switch (health.status) {
    case "missing":
      return "bioAF cannot find the results storage.";
    case "no_access":
      return "bioAF is not allowed to read the results storage.";
    case "not_confirmed":
      return "bioAF could not confirm the results storage belongs to this installation.";
    default: {
      const n = minutesUntil(health.next_attempt_at);
      return `The results storage did not respond. bioAF will try again in ${n} minute${n === 1 ? "" : "s"}.`;
    }
  }
}

/**
 * "New plots are not being added right now" (mockup M13). Existing plots are unaffected; the
 * notice disappears on the first successful scan. Admins can check again; checking never
 * recreates the results storage.
 */
export function PlotScanNotice({
  health,
  onRecovered,
}: {
  health: PlotScanHealth | null | undefined;
  onRecovered?: () => void;
}) {
  if (!health || health.status === "ok") return null;
  return <PlotScanNoticeBody initial={health} onRecovered={onRecovered} />;
}

function PlotScanNoticeBody({ initial, onRecovered }: { initial: PlotScanHealth; onRecovered?: () => void }) {
  const { canAccess, loading, failed } = usePermissions();
  const isAdmin = !loading && !failed && (canAccess("infrastructure", "deploy") || canAccess("infrastructure", "configure"));
  const [health, setHealth] = useState(initial);
  const [checkedNow, setCheckedNow] = useState(false);
  const [checking, setChecking] = useState(false);
  useEffect(() => setHealth(initial), [initial]);

  if (!health || health.status === "ok") return null;

  const checkAgain = async () => {
    setChecking(true);
    try {
      const next = await api.post<PlotScanHealth>("/api/plots/scan/check", {});
      setHealth(next);
      setCheckedNow(true);
      if (next.status === "ok") onRecovered?.();
    } catch (e) {
      logError("checking the results storage", e);
    } finally {
      setChecking(false);
    }
  };

  const n = minutesUntil(health.next_attempt_at);
  const sentences = [cause(health), "Plots already in the archive are not affected."];
  if (health.last_success_at) sentences.push(`Last successful check: ${formatWhen(health.last_success_at)}.`);
  sentences.push(
    isAdmin
      ? `Next automatic check in ${n} minute${n === 1 ? "" : "s"}.`
      : "Ask an administrator to check the results storage.",
  );

  return (
    <div role="status" className="mb-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
      <p className="font-medium">New plots are not being added right now.</p>
      <p className="mt-1">{sentences.join(" ")}</p>
      {checkedNow && <p className="mt-1">Checked just now. The results storage is still unavailable.</p>}
      {isAdmin && (
        <div className="mt-2 flex items-center gap-3">
          <Button size="sm" variant="secondary" onClick={() => void checkAgain()} disabled={checking}>
            Check again
          </Button>
          <Link href="/infrastructure/components" className="text-sm underline">
            Open Components
          </Link>
        </div>
      )}
    </div>
  );
}
