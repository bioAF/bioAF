"use client";

import { DatasetBrowser } from "@/components/data/DatasetBrowser";

export default function DataBrowserPage() {
  return (
    <main className="flex-1 overflow-y-auto p-6">
      <h1 className="text-2xl font-bold mb-6">Dataset Browser</h1>
      <DatasetBrowser />
    </main>
  );
}
