import { NOT_SET } from "@/lib/placeholders";

/**
 * A record's created/updated time, which can be null.
 *
 * Released database schemas allow these timestamps to be NULL, and nothing
 * records what a cleared one was, so the API returns null rather than an
 * invented time (owner decision, 2026-11-07). `new Date(null)` would render
 * as 1 January 1970; a missing time renders as the not-set placeholder.
 */
export function formatRecordedTime(value: string | null, style: "datetime" | "date" = "datetime"): string {
  if (!value) return NOT_SET;
  const date = new Date(value);
  return style === "date" ? date.toLocaleDateString() : date.toLocaleString();
}

/** A sort key that places a record with no recorded time after every dated one in a newest-first sort. */
export function recordedTimeValue(value: string | null): number {
  return value ? Date.parse(value) : Number.NEGATIVE_INFINITY;
}
