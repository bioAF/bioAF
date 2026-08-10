"use client";

import { useParams, useRouter } from "next/navigation";
import { SdrDetailView } from "@/components/lab-knowledge/SdrBrowser";

export default function DecisionRecordDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const sdrId = Number(params.id);

  return (
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
  );
}
