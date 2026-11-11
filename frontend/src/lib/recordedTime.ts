import { NOT_SET } from "@/lib/placeholders";

/**
 * How a recorded instant is shown, always in the browser's time zone. Each
 * form is what its screens printed before these were shared.
 *
 * - datetime / date: the browser's locale defaults.
 * - monthDayTime: the run list, "Mar 11 2:05 PM" (US English).
 * - fullWithSeconds: the run detail, "Mar 11, 2026 2:05:09 PM" (US English).
 * - monthDayYearTime: plot details, short month with year, browser's locale.
 *
 * A calendar date with no time of day (a billing day, a collection date) is
 * not an instant and does not belong here: converting it to the browser's zone
 * can move it to the previous day.
 */
export type RecordedTimeStyle = "datetime" | "date" | "monthDayTime" | "fullWithSeconds" | "monthDayYearTime";

const RECORDED_TIME_STYLES: Record<RecordedTimeStyle, (date: Date, timeZone?: string) => string> = {
  datetime: (d, timeZone) => d.toLocaleString(undefined, { timeZone }),
  date: (d, timeZone) => d.toLocaleDateString(undefined, { timeZone }),
  monthDayTime: (d, timeZone) =>
    d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone }) +
    " " +
    d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone }),
  fullWithSeconds: (d, timeZone) =>
    d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone }) +
    " " +
    d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit", timeZone }),
  monthDayYearTime: (d, timeZone) =>
    d.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZone }),
};

export interface RecordedTimeOptions {
  /** What a missing or unreadable time says; the not-set placeholder by default. */
  missing?: string;
  /** Only for tests: the browser's own zone is always what users see. */
  timeZone?: string;
}

/**
 * A record's created/updated time, which can be null.
 *
 * Released database schemas allow these timestamps to be NULL, and nothing
 * records what a cleared one was, so the API returns null rather than an
 * invented time (owner decision, 2026-11-07). `new Date(null)` would render
 * as 1 January 1970; a missing time renders as the not-set placeholder, or
 * the caller's own missing wording. An unreadable time is treated as missing
 * rather than printing "Invalid Date".
 */
export function formatRecordedTime(
  value: string | null | undefined,
  style: RecordedTimeStyle = "datetime",
  { missing = NOT_SET, timeZone }: RecordedTimeOptions = {},
): string {
  if (!value) return missing;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return missing;
  return RECORDED_TIME_STYLES[style](date, timeZone);
}

/** A sort key that places a record with no recorded time after every dated one in a newest-first sort. */
export function recordedTimeValue(value: string | null): number {
  return value ? Date.parse(value) : Number.NEGATIVE_INFINITY;
}
