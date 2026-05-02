"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";
import type { PipelineCatalog, PipelineCatalogListResponse } from "@/lib/types";

export default function PipelineCatalogPage() {
  const router = useRouter();
  const { canAccess, loading: permsLoading } = usePermissions();

  const [pipelines, setPipelines] = useState<PipelineCatalog[]>([]);
  const [loading, setLoading] = useState(true);

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

  function launchPipeline(p: PipelineCatalog) {
    if (p.source_type === "custom" && p.custom_pipeline_id != null) {
      router.push(`/pipelines/custom/${p.custom_pipeline_id}?launch=1`);
      return;
    }
    router.push(`/pipelines/launch/${encodeURIComponent(p.pipeline_key)}`);
  }

  function openPipeline(p: PipelineCatalog) {
    if (p.source_type === "custom" && p.custom_pipeline_id != null) {
      router.push(`/pipelines/custom/${p.custom_pipeline_id}`);
      return;
    }
    router.push(`/pipelines/launch/${encodeURIComponent(p.pipeline_key)}`);
  }

  const canCreateCustom = !permsLoading && canAccess("custom_pipelines", "create");

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
            <div>
              <h1 className="text-2xl font-bold">Pipeline Catalog</h1>
              <p className="text-sm text-gray-500 mt-1">
                Built-in NF-Core pipelines and your organization&apos;s custom pipelines.
              </p>
            </div>
            {canCreateCustom && (
              <button
                onClick={() => router.push("/pipelines/custom")}
                className="bg-bioaf-600 text-white px-4 py-2 rounded-md text-sm hover:bg-bioaf-700"
              >
                Manage Custom Pipelines
              </button>
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {pipelines.map((p) => {
              const isCustom = p.source_type === "custom";
              const versionLabel = isCustom
                ? p.latest_version_number != null
                  ? `v${p.latest_version_number}`
                  : "no versions"
                : `v${p.version || "latest"}`;
              return (
                <div
                  key={p.id}
                  onClick={() => openPipeline(p)}
                  className="bg-white rounded-lg shadow p-6 hover:shadow-md transition-shadow cursor-pointer"
                >
                  <div className="flex items-start justify-between mb-3">
                    <h3 className="font-semibold text-lg">{p.name}</h3>
                    <span className={`px-2 py-0.5 text-xs rounded-full ${
                      isCustom
                        ? "bg-blue-100 text-blue-700"
                        : "bg-green-100 text-green-700"
                    }`}>
                      {p.source_type}
                    </span>
                  </div>
                  <p className="text-sm text-gray-500 mb-4 line-clamp-2">{p.description || "No description"}</p>
                  <div className="flex items-center justify-between">
                    <div className="text-xs text-gray-400">
                      {isCustom && p.created_by_username ? (
                        <>
                          <span>by {p.created_by_username}</span>
                          <span className="mx-1.5">•</span>
                          <span>{versionLabel}</span>
                        </>
                      ) : (
                        <span>{versionLabel}</span>
                      )}
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); launchPipeline(p); }}
                      disabled={isCustom && p.latest_version_number == null}
                      className="bg-bioaf-600 text-white px-4 py-1.5 rounded text-sm hover:bg-bioaf-700 disabled:opacity-50"
                    >
                      Launch
                    </button>
                  </div>
                </div>
              );
            })}
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
