"use client";

import { Button } from "@/components/ui/Button";

/**
 * The full-screen panel that covers the app until it has booted.
 *
 * It exists as its own component because it now has two jobs, not one. Measured
 * on the deployed app 2026-08-07: a 500 on `/api/health/ready`, with every other
 * endpoint healthy, held the old splash forever. The whole user-visible UI was
 * the string "bioAF Loading bioAF..." -- no message, no retry, **zero focusable
 * elements**, and zero live regions, so a keyboard user had nothing to press and
 * a screen-reader user was told nothing at all.
 *
 * Waiting and failing look different now, and both are announced. `role="status"`
 * rather than `role="alert"`: this is a state the user is waiting on, not an
 * interruption, and it is on the wrapper so the region exists before the text
 * changes into it.
 *
 * Colours: this sits on `bg-gray-900` in both themes (the dark override shim
 * deliberately leaves gray-900 alone), so the lighter grey is the readable one
 * here, `text-gray-300` at ~9:1 rather than gray-500's ~3.7:1.
 */
export function BootSplash({
  failed = false,
  message,
  onRetry,
}: {
  /** True once a boot dependency has actually failed, not merely gone slow. */
  failed?: boolean;
  /** The plain sentence to show when `failed`. Never a technical detail. */
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900 p-6"
      data-testid="app-loading"
      role="status"
      aria-live="polite"
    >
      <div className="max-w-sm text-center">
        <div className="mb-4 text-3xl font-bold text-bioaf-400">bioAF</div>

        {failed ? (
          <>
            <p className="text-sm text-gray-300" data-testid="app-loading-failed">
              {message}
            </p>
            {onRetry && (
              <div className="mt-4">
                <Button variant="primary" size="sm" onClick={onRetry}>
                  Try again
                </Button>
              </div>
            )}
          </>
        ) : (
          <>
            <div
              className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-bioaf-400 border-t-transparent motion-reduce:animate-none"
              aria-hidden="true"
            />
            <p className="mt-3 text-sm text-gray-300">Loading bioAF...</p>
          </>
        )}
      </div>
    </div>
  );
}
