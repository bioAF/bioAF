import { NOT_SET } from "@/lib/placeholders";

/**
 * How long a run has taken: from its start to its completion, or to `nowMs`
 * while it is still going. The caller owns the clock (the run list ticks every
 * second for in-flight runs); this function only does the arithmetic.
 *
 * - compact (run list): "45s", "12m", "3h 5m".
 * - detail (run detail): "45s", "12m 30s", "3h 5m".
 *
 * No start, or an unreadable time, has no duration. An end before the start
 * (clock skew between server and browser) reads as zero.
 */
export function formatElapsed(
  startedAt: string | null | undefined,
  completedAt: string | null | undefined,
  nowMs: number,
  style: "compact" | "detail",
): string {
  if (!startedAt) return NOT_SET;
  const start = Date.parse(startedAt);
  const end = completedAt ? Date.parse(completedAt) : nowMs;
  if (Number.isNaN(start) || Number.isNaN(end)) return NOT_SET;
  const seconds = Math.max(0, Math.floor((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) {
    return style === "compact" ? `${Math.floor(seconds / 60)}m` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  }
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}
