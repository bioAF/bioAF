"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { isAuthenticated } from "@/lib/auth";

/**
 * Shared shell for every authenticated page. It mounts the Sidebar + Header ONCE
 * (pages used to hand-mount them 57 times, each re-opening the same flex wrapper
 * in every return branch), and hosts the single auth gate that redirects an
 * unauthenticated visitor to /login.
 *
 * The layout owns the outer chrome but NOT the `<main>`: each page renders its own
 * `<main>` so it keeps its per-page scroll/centered variant and its data-testid.
 * A page that used to render chrome-less (e.g. a bare loading spinner) now renders
 * inside this shell, closing the "chrome-less validation detail" inconsistency.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();

  // Redirect (not render-gate) so there is no server/client hydration mismatch:
  // isAuthenticated() reads localStorage, which is client-only. This matches the
  // per-page pattern it replaces.
  useEffect(() => {
    if (!isAuthenticated()) router.replace("/login");
  }, [router]);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        {children}
      </div>
    </div>
  );
}
