"use client";

import Link from "next/link";
import { useEffect } from "react";
import { Button } from "@/components/ui/Button";

/**
 * Error boundary for every authenticated page.
 *
 * Placed inside the `(app)` group so Next.js renders it INSIDE `(app)/layout.tsx`:
 * a page that throws keeps the Sidebar and Header, so the user can navigate away
 * instead of being stranded on a bare error screen.
 *
 * The raw message is deliberately not shown. It is a developer string (often an
 * unhandled `TypeError`), it can carry internal detail, and it tells the user
 * nothing they can act on. The `digest` is shown instead: it is the stable id
 * Next.js also writes to the server log, so a user can quote it and support can
 * find the exact occurrence.
 */
export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Keep the real error in the browser console for anyone with devtools open.
    console.error(error);
  }, [error]);

  return (
    <main className="flex flex-1 items-center justify-center overflow-y-auto px-6 py-16">
      <div className="w-full max-w-md">
        <h1 className="text-2xl font-semibold text-ink">Something went wrong</h1>
        <p className="mt-3 text-sm leading-relaxed text-ink-muted">
          This page failed to load. Your work has not been lost: nothing was saved or
          changed by this error.
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Button
            onClick={reset}>
            Try again
          </Button>
          <Link
            href="/dashboard"
            className="rounded-md border border-hairline px-4 py-2 text-sm font-medium text-ink-muted hover:bg-surface-muted"
          >
            Go to Dashboard
          </Link>
        </div>
        {error.digest && (
          <p className="mt-6 text-xs text-ink-subtle">
            Reference for support: <code className="font-mono">{error.digest}</code>
          </p>
        )}
      </div>
    </main>
  );
}
