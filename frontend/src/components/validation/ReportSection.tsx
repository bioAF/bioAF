"use client";

import { useEffect, type ReactNode } from "react";

import type { ReportCount } from "@/lib/validationReport";

/**
 * plan_8_2 section 4.2 (approved 2026-09-14): one of the report's four sections.
 *
 * A native disclosure, so the keyboard and a screen reader get the expanded state for free. Its summary
 * line (title, counts, one or two sentences) stays visible while the detail is collapsed; routine detail is
 * collapsed by default. The summaries and counts are the backend's.
 */
const TONE: Record<string, string> = {
  ok: "bg-emerald-50 text-emerald-700",
  bad: "bg-red-50 text-red-700",
  warn: "bg-amber-50 text-amber-700",
};

export function CountChips({ counts }: { counts: ReportCount[] }) {
  if (counts.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-1.5">
      {counts.map((count) => (
        <span
          key={count.label}
          className={`rounded px-1.5 py-0.5 text-xs ${TONE[count.tone ?? ""] ?? "bg-gray-100 text-gray-700"}`}
        >
          {count.label}
        </span>
      ))}
    </span>
  );
}

export function ReportSection({
  id,
  title,
  summary,
  counts,
  defaultOpen = false,
  children,
}: {
  id: string;
  title: string;
  summary?: string | null;
  counts: ReportCount[];
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  return (
    <details
      id={id}
      open={defaultOpen || undefined}
      data-testid={`report-section-${id}`}
      className="group mb-4 rounded-lg border border-gray-200 bg-surface"
    >
      <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-x-3 gap-y-1 rounded-lg px-4 py-3 [&::-webkit-details-marker]:hidden">
        <span aria-hidden="true" className="text-xs text-gray-500 transition-transform group-open:rotate-90 motion-reduce:transition-none">
          ▶
        </span>
        <h2 className="text-base font-semibold text-ink">{title}</h2>
        <span className="ml-auto">
          <CountChips counts={counts} />
        </span>
        {summary && <p className="basis-full pl-6 text-sm text-gray-700">{summary}</p>}
      </summary>
      <div className="space-y-5 border-t border-gray-100 px-4 py-4 sm:pl-10">{children}</div>
    </details>
  );
}

/** Open every disclosure around ``id`` and bring it into view. False when nothing on the page has that id. */
export function openTo(id: string): boolean {
  const target = document.getElementById(id);
  if (!target) return false;
  for (let node: HTMLElement | null = target; node; node = node.parentElement) {
    if (node.tagName === "DETAILS") (node as HTMLDetailsElement).open = true;
  }
  target.scrollIntoView?.({ block: "start" });
  return true;
}

/**
 * plan_8_2 section 4.2: a deep link to a finding or a claim opens the disclosures around it, whether it was
 * followed from the page or arrived in the address. ``ready`` is true once the report has rendered.
 */
export function useDisclosureLinks(ready: boolean) {
  useEffect(() => {
    if (!ready) return;
    const fromHash = () => {
      const id = decodeURIComponent(window.location.hash.slice(1));
      if (id) openTo(id);
    };
    const onClick = (event: MouseEvent) => {
      const anchor = (event.target as Element | null)?.closest?.('a[href^="#"]');
      const id = anchor?.getAttribute("href")?.slice(1);
      if (id && openTo(id)) {
        event.preventDefault();
        window.history.replaceState(null, "", `#${id}`);
      }
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    document.addEventListener("click", onClick);
    return () => {
      window.removeEventListener("hashchange", fromHash);
      document.removeEventListener("click", onClick);
    };
  }, [ready]);
}
