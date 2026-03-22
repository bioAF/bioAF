"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import { api } from "@/lib/api";
import type { TemplateNotebookResponse, TemplateNotebookListResponse } from "@/lib/types";

export default function TemplateNotebooksPage() {
  const router = useRouter();
  const user = getCurrentUser();
  const canClone = user?.role === "admin" || user?.role === "comp_bio";

  const [templates, setTemplates] = useState<TemplateNotebookResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [cloneTarget, setCloneTarget] = useState<TemplateNotebookResponse | null>(null);
  const [cloneName, setCloneName] = useState("");
  const [cloneExperimentId, setCloneExperimentId] = useState("");
  const [cloning, setCloning] = useState(false);

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    loadTemplates();
  }, [router]);

  async function loadTemplates() {
    try {
      const data = await api.get<TemplateNotebookListResponse>("/api/template-notebooks");
      setTemplates(data.notebooks);
    } catch {} finally { setLoading(false); }
  }

  async function handleClone() {
    if (!cloneTarget || !cloneName) return;
    setCloning(true);
    try {
      const result = await api.post<{ output_path: string }>(
        `/api/template-notebooks/${cloneTarget.id}/clone`,
        {
          new_name: cloneName,
          experiment_id: cloneExperimentId ? parseInt(cloneExperimentId) : null,
          parameters: {},
        },
      );
      setCloneTarget(null);
      setCloneName("");
      setCloneExperimentId("");
      alert(`Notebook cloned to: ${result.output_path}`);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Clone failed");
    } finally { setCloning(false); }
  }

  const categoryBadge: Record<string, string> = {
    qc: "bg-red-100 text-red-700",
    normalization: "bg-blue-100 text-blue-700",
    clustering: "bg-green-100 text-green-700",
    differential_expression: "bg-purple-100 text-purple-700",
    trajectory: "bg-orange-100 text-orange-700",
  };

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
          <h1 className="text-2xl font-bold mb-6">Template Notebooks</h1>
          <p className="text-sm text-gray-500 mb-6">
            Pre-built analysis workflows for scRNA-seq data. Clone a template and customize for your experiment.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {templates.map((tmpl) => (
              <div key={tmpl.id} className="bg-white rounded-lg shadow p-6 hover:shadow-md transition-shadow">
                <div className="flex items-start justify-between mb-3">
                  <h3 className="font-semibold text-lg">{tmpl.name}</h3>
                  {tmpl.category && (
                    <span className={`px-2 py-0.5 text-xs rounded-full ${categoryBadge[tmpl.category] || "bg-gray-100 text-gray-700"}`}>
                      {tmpl.category.replace("_", " ")}
                    </span>
                  )}
                </div>
                <p className="text-sm text-gray-500 mb-4 line-clamp-3">{tmpl.description || "No description"}</p>
                <div className="flex items-center justify-between">
                  {tmpl.compatible_with && (
                    <span className="text-xs text-gray-400">Compatible: {tmpl.compatible_with}</span>
                  )}
                  {canClone && (
                    <button
                      onClick={() => { setCloneTarget(tmpl); setCloneName(`${tmpl.name.toLowerCase().replace(/\s+/g, "_")}_copy`); }}
                      className="bg-bioaf-600 text-white px-4 py-1.5 rounded text-sm hover:bg-bioaf-700"
                    >
                      Clone
                    </button>
                  )}
                </div>
              </div>
            ))}
            {templates.length === 0 && (
              <div className="col-span-full text-center py-12 text-gray-400">No templates available</div>
            )}
          </div>

          {/* Clone Modal */}
          {cloneTarget && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
              <div className="bg-white rounded-lg shadow-xl p-6 w-96">
                <h3 className="font-semibold text-lg mb-4">Clone Template</h3>
                <div className="space-y-3">
                  <div>
                    <label className="text-sm text-gray-500 block mb-1">Template</label>
                    <p className="font-medium">{cloneTarget.name}</p>
                  </div>
                  <div>
                    <label className="text-sm text-gray-500 block mb-1">Notebook Name</label>
                    <input
                      value={cloneName}
                      onChange={(e) => setCloneName(e.target.value)}
                      className="w-full border rounded px-3 py-2 text-sm"
                    />
                  </div>
                  <div>
                    <label className="text-sm text-gray-500 block mb-1">Experiment ID (optional)</label>
                    <input
                      value={cloneExperimentId}
                      onChange={(e) => setCloneExperimentId(e.target.value)}
                      placeholder="Auto-parameterize for experiment"
                      className="w-full border rounded px-3 py-2 text-sm"
                    />
                  </div>
                </div>
                <div className="flex gap-2 mt-6">
                  <button
                    onClick={handleClone}
                    disabled={cloning || !cloneName}
                    className="flex-1 bg-bioaf-600 text-white py-2 rounded text-sm hover:bg-bioaf-700 disabled:opacity-50"
                  >
                    {cloning ? "Cloning..." : "Clone & Open"}
                  </button>
                  <button
                    onClick={() => setCloneTarget(null)}
                    className="flex-1 border py-2 rounded text-sm"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            </div>
          )}
          </>
          )}
        </main>
      </div>
    </div>
  );
}
