/** Compact relative time for dashboard widget rows (e.g. "3h ago"). */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

/** True if `iso` is within the last `hours` hours. Used for client-side windows. */
export function withinHours(iso: string | null | undefined, hours: number): boolean {
  if (!iso) return false;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return false;
  return Date.now() - then <= hours * 3600 * 1000;
}
