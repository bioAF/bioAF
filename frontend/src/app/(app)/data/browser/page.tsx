"use client";

import { DatasetBrowser } from "@/components/data/DatasetBrowser";

export default function DataBrowserPage() {
  return (
    <main className="flex-1 overflow-y-auto p-6">
      <h1 className="text-2xl font-bold mb-1">Dataset Browser</h1>
      <p data-testid="page-description" className="text-sm text-gray-500 mb-6">
        Search experiments by organism, molecule type and instrument, and add the ones you want to a project.
      </p>
      <DatasetBrowser />
    </main>
  );
}
