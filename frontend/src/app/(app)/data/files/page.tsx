"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { FileBrowser } from "@/components/files/FileBrowser";

export default function DataFilesPage() {
  return (
    <Suspense fallback={null}>
      <DataFilesPageInner />
    </Suspense>
  );
}

function DataFilesPageInner() {
  const searchParams = useSearchParams();
  const fileParam = searchParams?.get("file");
  const focusFileId = fileParam && /^\d+$/.test(fileParam) ? Number(fileParam) : undefined;

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <h1 className="text-2xl font-bold mb-1">Files</h1>
      <p data-testid="page-description" className="text-sm text-gray-500 mb-6">
        Every file registered in this instance, filterable by project, experiment and pipeline run.
      </p>
      <FileBrowser
        showSearch
        showProjectFilter
        showExperimentFilter
        showReconcile
        focusFileId={focusFileId}
      />
    </main>
  );
}
