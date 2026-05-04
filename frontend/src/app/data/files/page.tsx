"use client";

import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { FileBrowser } from "@/components/files/FileBrowser";

export default function DataFilesPage() {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">Files</h1>
          <FileBrowser
            showSearch
            showProjectFilter
            showExperimentFilter
            showReconcile
          />
        </main>
      </div>
    </div>
  );
}
