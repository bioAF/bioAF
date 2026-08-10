/**
 * What a page shows while its content is on the way.
 *
 * The app had 10 skeletons against 96 spinners. A spinner says only "wait": it
 * is the same for a three-row table and a full dashboard, it gives no sense of
 * how much is coming, and the layout jumps when the real thing lands. A
 * skeleton in the shape of the content answers both.
 *
 * The shapes are `aria-hidden`: they are a drawing of content that does not
 * exist yet, and a screen reader reading a dozen empty boxes is worse than
 * silence. The single `role="status"` line carries the meaning instead, which
 * is what the spinner did before.
 */
export function ContentLoading({
  message,
  variant = "block",
  rows = 5,
}: {
  message?: string;
  /** The shape of what is coming. `block` is the safe default. */
  variant?: "block" | "table" | "cards";
  /** How many rows or cards to draw. */
  rows?: number;
}) {
  const bar = "animate-pulse rounded bg-elevated";

  return (
    <div className="py-6">
      <div role="status" aria-live="polite" className="sr-only">
        {message ?? "Loading"}
      </div>

      {variant === "table" && (
        <div className="space-y-2">
          {Array.from({ length: rows }).map((_, i) => (
            <div
              key={i}
              data-testid="skeleton-row"
              aria-hidden="true"
              className={`flex items-center gap-4 ${bar} h-10 px-4`}
            />
          ))}
        </div>
      )}

      {variant === "cards" && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: rows }).map((_, i) => (
            <div
              key={i}
              data-testid="skeleton-card"
              aria-hidden="true"
              className="rounded-lg bg-surface p-6 shadow"
            >
              <div className={`${bar} mb-3 h-5 w-2/3`} />
              <div className={`${bar} mb-2 h-3 w-full`} />
              <div className={`${bar} h-3 w-4/5`} />
            </div>
          ))}
        </div>
      )}

      {variant === "block" && (
        <div aria-hidden="true" className="space-y-3">
          <div className={`${bar} h-5 w-1/3`} />
          <div className={`${bar} h-3 w-full`} />
          <div className={`${bar} h-3 w-11/12`} />
          <div className={`${bar} h-3 w-3/4`} />
        </div>
      )}

      {message && <p className="mt-4 text-center text-sm text-ink-subtle">{message}</p>}
    </div>
  );
}
