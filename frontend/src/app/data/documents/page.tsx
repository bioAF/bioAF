"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { api } from "@/lib/api";
import type { DocumentResponse, DocumentSearchResponse } from "@/lib/types";

export default function DataDocumentsPage() {
  const [documents, setDocuments] = useState<DocumentResponse[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchDocuments = useCallback(async () => {
    setLoading(true);
    try {
      if (searchQuery) {
        const data = await api.get<DocumentSearchResponse>(
          `/api/documents/search?query=${encodeURIComponent(searchQuery)}`
        );
        setDocuments(data.documents);
      } else {
        const data = await api.get<DocumentSearchResponse>("/api/documents");
        setDocuments(data.documents);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [searchQuery]);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files?.[0]) return;
    try {
      await api.upload<DocumentResponse>("/api/documents/upload", e.target.files[0]);
      fetchDocuments();
    } catch {
      // ignore
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Delete this document?")) return;
    try {
      await api.delete(`/api/documents/${id}`);
      fetchDocuments();
    } catch {
      // ignore
    }
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">Documents</h1>

          <div className="space-y-4">
            <div className="flex gap-4">
              <input
                type="text"
                placeholder="Search document contents..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="flex-1 px-3 py-2 border border-gray-300 rounded-md text-sm"
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700"
              >
                Upload Document
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,.docx,.txt,.md"
                onChange={handleUpload}
                className="hidden"
              />
            </div>

            {loading ? (
              <p className="text-gray-400 text-sm">Loading...</p>
            ) : documents.length === 0 ? (
              <p className="text-gray-400 text-sm">No documents found.</p>
            ) : (
              <div className="bg-white rounded-lg shadow divide-y divide-gray-200">
                {documents.map((doc) => (
                  <div
                    key={doc.id}
                    className="p-4 flex items-center justify-between hover:bg-gray-50"
                  >
                    <div>
                      <p className="font-medium text-sm">{doc.title}</p>
                      <p className="text-xs text-gray-400">
                        Uploaded{" "}
                        {new Date(doc.created_at).toLocaleDateString()}
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <a
                        href={`/api/documents/${doc.id}/download`}
                        className="text-blue-600 text-sm hover:underline"
                      >
                        Download
                      </a>
                      <button
                        onClick={() => handleDelete(doc.id)}
                        className="text-red-500 text-sm hover:underline"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
