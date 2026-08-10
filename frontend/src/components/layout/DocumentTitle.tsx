"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { titleForPath } from "@/lib/pageTitle";

/**
 * Names the browser tab after the page.
 *
 * Renders nothing. Every page in the app is a client component, so the static
 * `metadata` export Next.js would normally carry this with is unavailable on all
 * but the root layout, which is why every route reported the one title set
 * there. Mounted once in the root layout, this follows the pathname instead,
 * which also covers client-side navigation, where no document is loaded at all.
 *
 * This only works because the root layout renders a plain `<title>` element
 * rather than declaring `metadata.title`. Next re-asserts a metadata title
 * asynchronously after hydration, so with one declared, the first deploy of this
 * component set the right title and had it reverted a moment later: the tab was
 * correct after a client-side nav and wrong after a full page load. See the
 * comment on that element before reinstating `metadata.title`.
 */
export function DocumentTitle() {
  const pathname = usePathname();

  useEffect(() => {
    document.title = titleForPath(pathname);
  }, [pathname]);

  return null;
}
