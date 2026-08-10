import Link from "next/link";

/**
 * Global 404 for any URL that matches no route.
 *
 * This sits at the root, OUTSIDE the `(app)` group, because Next.js resolves an
 * unmatched URL against the root not-found boundary: a `not-found.tsx` inside
 * `(app)` would only fire for an explicit `notFound()` call from a page in that
 * group, never for a mistyped path. That means the authenticated shell (Sidebar +
 * Header, which live in `(app)/layout.tsx`) is deliberately NOT available here, so
 * this page carries its own wayfinding instead of inheriting the nav.
 */
export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-16">
      <div className="w-full max-w-md">
        <p className="text-sm font-medium text-ink-subtle">404</p>
        <h1 className="mt-2 text-2xl font-semibold text-ink">Page not found</h1>
        <p className="mt-3 text-sm leading-relaxed text-ink-muted">
          This address does not match anything in bioAF. It may have been renamed, or the
          link that brought you here may be out of date.
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Link
            href="/dashboard"
            className="rounded-md bg-bioaf-600 px-4 py-2 text-sm font-medium text-white hover:bg-bioaf-700"
          >
            Go to Dashboard
          </Link>
          <Link
            href="/search"
            className="rounded-md border border-hairline px-4 py-2 text-sm font-medium text-ink-muted hover:bg-surface-muted"
          >
            Search bioAF
          </Link>
        </div>
      </div>
    </main>
  );
}
