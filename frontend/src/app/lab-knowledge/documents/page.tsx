"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { LabDocumentBrowser } from "@/components/lab-knowledge/LabDocumentBrowser";

export default function LabDocumentsPage() {
  return (
    <Suspense fallback={null}>
      <LabDocumentsPageInner />
    </Suspense>
  );
}

function LabDocumentsPageInner() {
  const searchParams = useSearchParams();
  const docParam = searchParams?.get("doc");
  const focusDocId = docParam && /^\d+$/.test(docParam) ? Number(docParam) : undefined;

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-1">Lab Documents</h1>
          <p className="text-sm text-gray-500 mb-6">
            Operational and institutional documents for your lab. Distinct from experiment-linked
            files in Data &amp; Files.
          </p>
          <LabDocumentBrowser focusDocId={focusDocId} />
        </main>
      </div>
    </div>
  );
}
