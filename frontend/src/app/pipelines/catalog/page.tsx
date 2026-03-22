"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import { api } from "@/lib/api";
import type { PipelineCatalog, PipelineCatalogListResponse, PipelineAddRequest } from "@/lib/types";

export default function PipelineCatalogPage() {
  const router = useRouter();
  const user = getCurrentUser();
  const isAdmin = user?.role === "admin";

  const [pipelines, setPipelines] = useState<PipelineCatalog[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAddForm, setShowAddForm] = useState(false);
  const [addForm, setAddForm] = useState<PipelineAddRequest>({ name: "", source_url: "" });

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    loadPipelines();
  }, [router]);

  async function loadPipelines() {
    try {
      const data = await api.get<PipelineCatalogListResponse>("/api/pipelines");
      setPipelines(data.pipelines);
    } catch {} finally { setLoading(false); }
  }

  async function handleAddCustom() {
    try {
      await api.post("/api/pipelines/custom", addForm);
      setShowAddForm(false);
      setAddForm({ name: "", source_url: "" });
      loadPipelines();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to add pipeline");
    }
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <ContentLoading />
          ) : (
          <>
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-2xl font-bold">Pipeline Catalog</h1>
            {isAdmin && (
              <button
                onClick={() => setShowAddForm(!showAddForm)}
                className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
              >
                Add Custom Pipeline
              </button>
            )}
          </div>

          {showAddForm && (
            <div className="bg-white rounded-lg shadow p-4 mb-6">
              <h3 className="font-semibold mb-3">Add Custom Pipeline</h3>
              <div className="grid grid-cols-2 gap-3">
                <input placeholder="Pipeline Name *" value={addForm.name} onChange={(e) => setAddForm({ ...addForm, name: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                <input placeholder="Git URL *" value={addForm.source_url} onChange={(e) => setAddForm({ ...addForm, source_url: e.target.value })} className="border rounded px-3 py-2 text-sm" />
                <input placeholder="Version (optional)" value={addForm.version ?? ""} onChange={(e) => setAddForm({ ...addForm, version: e.target.value || null })} className="border rounded px-3 py-2 text-sm" />
                <input placeholder="Description (optional)" value={addForm.description ?? ""} onChange={(e) => setAddForm({ ...addForm, description: e.target.value || null })} className="border rounded px-3 py-2 text-sm" />
              </div>
              <div className="flex gap-2 mt-3">
                <button onClick={handleAddCustom} className="bg-bioaf-600 text-white px-4 py-1.5 rounded text-sm">Add</button>
                <button onClick={() => setShowAddForm(false)} className="border px-4 py-1.5 rounded text-sm">Cancel</button>
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {pipelines.map((p) => (
              <div key={p.id} className="bg-white rounded-lg shadow p-6 hover:shadow-md transition-shadow">
                <div className="flex items-start justify-between mb-3">
                  <h3 className="font-semibold text-lg">{p.name}</h3>
                  <span className={`px-2 py-0.5 text-xs rounded-full ${
                    p.source_type === "nf-core" ? "bg-green-100 text-green-700" : "bg-blue-100 text-blue-700"
                  }`}>
                    {p.source_type}
                  </span>
                </div>
                <p className="text-sm text-gray-500 mb-4 line-clamp-2">{p.description || "No description"}</p>
                <div className="flex items-center justify-between">
                  <span className="text-xs text-gray-400">v{p.version || "latest"}</span>
                  <button
                    onClick={() => router.push(`/pipelines/launch/${encodeURIComponent(p.pipeline_key)}`)}
                    className="bg-bioaf-600 text-white px-4 py-1.5 rounded text-sm hover:bg-bioaf-700"
                  >
                    Launch
                  </button>
                </div>
              </div>
            ))}
            {pipelines.length === 0 && (
              <div className="col-span-full text-center py-12 text-gray-400">No pipelines available</div>
            )}
          </div>
          </>
          )}
        </main>
      </div>
    </div>
  );
}
