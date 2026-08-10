"use client";

import type { ReactNode } from "react";

/**
 * A panel on the app's surface colour.
 *
 * 240 places spelled this by hand as `bg-white rounded-lg shadow p-6` and
 * variants, which is why dark mode had to be a CSS override shim: `bg-white`
 * says white, so something else had to take it back. `bg-surface` resolves per
 * theme, so the card is correct in both without an override.
 *
 * `title` is optional on purpose. A card with a heading is a region worth
 * announcing; a card without one is a box, and `role="region"` on a box just
 * adds an unnamed landmark for a screen reader to walk past.
 */
export function Card({
  title,
  actions,
  padding = "md",
  className = "",
  children,
}: {
  title?: ReactNode;
  /** Controls that belong to the card's header, laid out opposite the title. */
  actions?: ReactNode;
  padding?: "none" | "sm" | "md";
  className?: string;
  children: ReactNode;
}) {
  const pad = padding === "none" ? "" : padding === "sm" ? "p-4" : "p-6";
  const surface = `bg-surface rounded-lg shadow ${pad} ${className}`.trim();

  if (!title) {
    return <div className={surface}>{children}</div>;
  }

  return (
    <section className={surface} aria-label={typeof title === "string" ? title : undefined}>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="font-semibold text-ink">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}
