"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { SdrDetailView } from "@/components/lab-knowledge/SdrBrowser";
import { isAuthenticated } from "@/lib/auth";

export default function DecisionRecordDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const sdrId = Number(params.id);

  useEffect(() => {
    if (!isAuthenticated()) router.push("/login");
  }, [router]);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <button
            onClick={() => router.push("/lab-knowledge/decision-records")}
            className="text-bioaf-700 hover:underline text-sm mb-4"
          >
            ← Back to decision records
          </button>
          <SdrDetailView
            sdrId={sdrId}
            onDeleted={() => router.push("/lab-knowledge/decision-records")}
          />
        </main>
      </div>
    </div>
  );
}
