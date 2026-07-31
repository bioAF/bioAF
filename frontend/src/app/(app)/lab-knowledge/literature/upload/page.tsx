"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { literature } from "@/lib/literature";

export default function LiteratureUploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [doi, setDoi] = useState("");
  const [journal, setJournal] = useState("");
  const [abstract, setAbstract] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) {
      setError("Pick a PDF first.");
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const extra: Record<string, string> = {};
      if (title) extra.title = title;
      if (doi) extra.doi = doi;
      if (journal) extra.journal = journal;
      if (abstract) extra.abstract = abstract;
      const paper = await literature.uploadPaper(file, extra);
      router.push(`/lab-knowledge/literature/papers/${paper.id}`);
    } catch (e) {
      const message =
        e instanceof Error ? e.message : "Upload failed.";
      setError(message);
    } finally {
      setUploading(false);
    }
  }

  return (
        <main className="flex-1 overflow-y-auto p-6 max-w-3xl">
          <h1 className="text-2xl font-bold mb-6">Upload Paper</h1>
          <form
            onSubmit={handleSubmit}
            className="space-y-4 bg-white rounded shadow p-6"
          >
            <div>
              <label className="block text-sm font-medium mb-1">PDF file</label>
              <input
                type="file"
                accept="application/pdf"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="block w-full"
                required
              />
            </div>
            <p className="text-xs text-gray-500">
              The backend extracts title, authors, DOI, and abstract from the
              PDF. Fill in the optional fields below only when you want to
              override what is extracted.
            </p>
            <div>
              <label className="block text-sm font-medium mb-1">
                Title (optional override)
              </label>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full border border-gray-300 rounded px-3 py-2"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">
                DOI (optional override)
              </label>
              <input
                value={doi}
                onChange={(e) => setDoi(e.target.value)}
                className="w-full border border-gray-300 rounded px-3 py-2"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">
                Journal (optional)
              </label>
              <input
                value={journal}
                onChange={(e) => setJournal(e.target.value)}
                className="w-full border border-gray-300 rounded px-3 py-2"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">
                Abstract (optional)
              </label>
              <textarea
                value={abstract}
                onChange={(e) => setAbstract(e.target.value)}
                rows={4}
                className="w-full border border-gray-300 rounded px-3 py-2"
              />
            </div>
            {error && <div className="text-sm text-red-700">{error}</div>}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => router.push("/lab-knowledge/literature")}
                className="border border-gray-300 px-4 py-2 rounded hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={uploading || !file}
                className="bg-bioaf-600 text-white px-4 py-2 rounded hover:bg-bioaf-700 disabled:opacity-50"
              >
                {uploading ? "Uploading..." : "Upload"}
              </button>
            </div>
          </form>
        </main>
  );
}
