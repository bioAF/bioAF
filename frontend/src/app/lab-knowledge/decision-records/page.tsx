"use client";

import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";

export default function DecisionRecordsPage() {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-1">Scientific Decision Records</h1>
          <p className="text-sm text-gray-500">Coming soon.</p>
        </main>
      </div>
    </div>
  );
}
