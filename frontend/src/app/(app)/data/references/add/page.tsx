"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { UploadReferenceForm } from "@/components/references/UploadReferenceForm";
import { UrlImportReferenceForm } from "@/components/references/UrlImportReferenceForm";

type Mode = "upload" | "url";

export default function AddReferencePage() {
  // useSearchParams forces client-side rendering and must be wrapped in a
  // Suspense boundary for Next.js's prerender pass to skip this page cleanly.
  return (
    <Suspense fallback={null}>
      <AddReferenceContent />
    </Suspense>
  );
}

function AddReferenceContent() {
  const router = useRouter();
  const search = useSearchParams();
  const lockedName = search?.get("name") ?? "";
  const lockedCategory = search?.get("category") ?? "";
  const lockedScope = search?.get("scope") ?? "";
  const initialMode: Mode = search?.get("mode") === "url" ? "url" : "upload";
  const [mode, setMode] = useState<Mode>(initialMode);

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <h1 className="text-2xl font-bold mb-2">Add Reference Data</h1>
      <p className="text-sm text-gray-600 mb-6">
        Choose how to bring this reference into bioAF: upload files directly from
        your machine, or have the server pull them from a public URL in the
        background.
      </p>

      <div className="inline-flex rounded-md border border-gray-300 overflow-hidden mb-6">
        <button
          type="button"
          onClick={() => setMode("upload")}
          aria-pressed={mode === "upload"}
          className={`px-4 py-2 text-sm ${
            mode === "upload"
              ? "bg-bioaf-600 text-white"
              : "bg-white text-gray-700 hover:bg-gray-50"
          }`}
        >
          Upload
        </button>
        <button
          type="button"
          onClick={() => setMode("url")}
          aria-pressed={mode === "url"}
          className={`px-4 py-2 text-sm border-l border-gray-300 ${
            mode === "url"
              ? "bg-bioaf-600 text-white"
              : "bg-white text-gray-700 hover:bg-gray-50"
          }`}
        >
          URL Import
        </button>
      </div>

      {mode === "upload" ? (
        <UploadReferenceForm
          lockedName={lockedName || undefined}
          lockedCategory={lockedCategory || undefined}
          lockedScope={lockedScope || undefined}
          onCreated={(id) => router.push(`/data/references/${id}`)}
          onCancel={() => router.push("/data/references")}
        />
      ) : (
        <UrlImportReferenceForm
          lockedName={lockedName || undefined}
          lockedCategory={lockedCategory || undefined}
          lockedScope={lockedScope || undefined}
          onStarted={(id) => router.push(`/data/references/${id}`)}
          onCancel={() => router.push("/data/references")}
        />
      )}
    </main>
  );
}
