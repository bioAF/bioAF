"use client";

import { LabDocumentBrowser } from "@/components/lab-knowledge/LabDocumentBrowser";

export default function LabDocumentsPage() {
  return (
    <main className="flex-1 overflow-y-auto p-6">
      <h1 className="text-2xl font-bold mb-1">Lab Documents</h1>
      <p data-testid="page-description" className="text-sm text-gray-500 mb-6">
        Operational and institutional documents for your lab. Distinct from experiment-linked
        files in Data &amp; Files.
      </p>
      <LabDocumentBrowser />
    </main>
  );
}
