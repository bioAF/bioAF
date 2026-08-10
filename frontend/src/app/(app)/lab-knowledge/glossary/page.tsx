"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { LabGlossaryBrowser } from "@/components/lab-knowledge/LabGlossaryBrowser";

export default function LabGlossaryPage() {
  return (
    <Suspense fallback={null}>
      <LabGlossaryPageInner />
    </Suspense>
  );
}

function LabGlossaryPageInner() {
  const searchParams = useSearchParams();
  const termParam = searchParams?.get("term");
  const focusTermId = termParam && /^\d+$/.test(termParam) ? Number(termParam) : undefined;

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <h1 className="text-2xl font-bold mb-1">Lab Glossary</h1>
      <p data-testid="page-description" className="text-sm text-gray-500 mb-6">
        A governed, searchable dictionary of your lab&apos;s terminology. Populate it manually,
        by CSV import, or with an AI-assisted scan; all proposals are reviewed before they are
        added.
      </p>
      <LabGlossaryBrowser focusTermId={focusTermId} />
    </main>
  );
}
