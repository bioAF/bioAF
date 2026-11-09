"use client";

import { Button } from "@/components/ui/Button";

/**
 * Says that a background refresh failed and what is on screen is the last good answer. Same
 * pattern as the pipeline runs banner: the rows are kept, because a transient failure should not
 * blank a list the user is reading, but they are labelled so they do not read as current.
 */
export function StaleNotice({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <div
      data-testid="stale-notice"
      role="status"
      className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-800 flex items-center justify-between gap-3"
    >
      <span>
        {what} could not be refreshed. What is shown is from the last successful load and may be out of date.
      </span>
      <Button variant="secondary" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}
