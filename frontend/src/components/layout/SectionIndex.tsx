"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useVisibleNavSections } from "@/hooks/useVisibleNavSections";
import { ContentLoading } from "@/components/shared/ContentLoading";

/**
 * What a bare section URL does.
 *
 * `/settings`, `/infrastructure` and `/lab-knowledge` are section headings in
 * the sidebar, not pages, so nothing in the app linked to them and typing one
 * produced a 404. They are exactly the URLs a person guesses, or bookmarks by
 * trimming a path back.
 *
 * The destination is the first child this user can actually reach, which is
 * why it comes from the same gate the sidebar uses rather than a static
 * redirect: a static one would send a viewer to a page their role forbids.
 */
export function SectionIndex({ section }: { section: string }) {
  const router = useRouter();
  const { loading, firstChildPath } = useVisibleNavSections();
  const destination = loading ? null : firstChildPath(section);

  useEffect(() => {
    if (destination) router.replace(destination);
  }, [destination, router]);

  if (loading || destination) {
    // Redirecting. A spinner rather than a flash of the message below.
    return (
      <main className="flex-1 overflow-y-auto p-6">
        <ContentLoading />
      </main>
    );
  }

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-md rounded-lg bg-surface p-8 text-center shadow">
        <h1 className="mb-2 text-lg font-semibold text-ink">
          There is nothing in {section} for your account
        </h1>
        <p className="mb-4 text-sm text-ink-subtle">
          Every page in this section needs a permission your role does not have. An admin can
          change that.
        </p>
        <Link href="/dashboard" className="text-sm text-bioaf-700 underline">
          Go to the dashboard
        </Link>
      </div>
    </main>
  );
}
