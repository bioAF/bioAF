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
 * there. Mounted once in the app shell, this follows the pathname instead, which
 * also covers client-side navigation, where no document is loaded at all.
 */
export function DocumentTitle() {
  const pathname = usePathname();

  useEffect(() => {
    document.title = titleForPath(pathname);
  }, [pathname]);

  return null;
}
