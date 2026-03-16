"use client";

import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { DatasetBrowser } from "@/components/data/DatasetBrowser";

export default function DataBrowserPage() {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">Dataset Browser</h1>
          <DatasetBrowser />
        </main>
      </div>
    </div>
  );
}
