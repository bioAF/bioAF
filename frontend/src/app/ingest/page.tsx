"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { isAuthenticated } from "@/lib/auth";
import { api } from "@/lib/api";
import { ContentLoading } from "@/components/shared/ContentLoading";
import type { IngestEvent, UnclaimedEntity } from "@/lib/types";

export default function IngestDashboardPage() {
  const router = useRouter();
  const [events, setEvents] = useState<IngestEvent[]>([]);
  const [unclaimed, setUnclaimed] = useState<UnclaimedEntity[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"events" | "unclaimed" | "simulate">("events");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  // Simulate state
  const [simFilenames, setSimFilenames] = useState("");
  const [simBucket, setSimBucket] = useState("bioaf-ingest-demo");
  const [simResults, setSimResults] = useState<IngestEvent[]>([]);

  // Claim state
  const [claimTarget, setClaimTarget] = useState<UnclaimedEntity | null>(null);
  const [claimEntityId, setClaimEntityId] = useState("");

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    loadData();
  }, [router]);

  const loadData = async () => {
    try {
      const [evts, unc] = await Promise.all([
        api.get<IngestEvent[]>("/api/ingest/events?limit=50"),
        api.get<UnclaimedEntity[]>("/api/ingest/unclaimed"),
      ]);
      setEvents(evts);
      setUnclaimed(unc);
    } catch {
      setError("Failed to load ingest data");
    } finally {
      setLoading(false);
    }
  };

  const handleSimulate = async () => {
    setError("");
    setMessage("");
    try {
      const filenames = simFilenames.split("\n").filter(Boolean);
      const results = await api.post<IngestEvent[]>("/api/ingest/simulate", {
        filenames,
        source_bucket: simBucket,
      });
      setSimResults(results);
      setMessage(`Simulated ${results.length} file(s)`);
      await loadData();
    } catch {
      setError("Simulation failed");
    }
  };

  const handleClaim = async (entity: UnclaimedEntity) => {
    setError("");
    setMessage("");
    if (!claimEntityId) { setError("Enter a target entity ID"); return; }
    try {
      await api.post(`/api/ingest/claim/${entity.entity_type}/${entity.entity_id}`, {
        target_entity_id: parseInt(claimEntityId),
      });
      setMessage(`${entity.entity_type} "${entity.name}" claimed successfully`);
      setClaimTarget(null);
      setClaimEntityId("");
      await loadData();
    } catch {
      setError("Claim failed");
    }
  };

  const statusColor = (status: string) => {
    switch (status) {
      case "cataloged": return "bg-green-100 text-green-700";
      case "unmatched": return "bg-red-100 text-red-700";
      case "multiple_matches": return "bg-yellow-100 text-yellow-700";
      case "duplicate": return "bg-gray-100 text-gray-600";
      default: return "bg-gray-100 text-gray-600";
    }
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="max-w-6xl mx-auto">
            <div className="flex justify-between items-center mb-6">
              <h1 className="text-2xl font-bold text-gray-900">Ingest Dashboard</h1>
              {unclaimed.length > 0 && (
                <span className="px-3 py-1 bg-yellow-100 text-yellow-800 rounded-full text-sm font-medium">
                  {unclaimed.length} unclaimed entit{unclaimed.length === 1 ? "y" : "ies"}
                </span>
              )}
            </div>

            {error && <div className="mb-4 p-3 bg-red-50 text-red-700 rounded-lg">{error}</div>}
            {message && <div className="mb-4 p-3 bg-green-50 text-green-700 rounded-lg">{message}</div>}

            {/* Tabs */}
            <div className="flex gap-1 mb-6 border-b">
              {(["events", "unclaimed", "simulate"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
                    tab === t
                      ? "border-bioaf-600 text-bioaf-600"
                      : "border-transparent text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {t === "events" ? "Recent Events" : t === "unclaimed" ? `Unclaimed (${unclaimed.length})` : "Simulate Ingest"}
                </button>
              ))}
            </div>

            {loading ? (
              <ContentLoading />
            ) : tab === "events" ? (
              <div className="bg-white border rounded-lg overflow-hidden">
                <table className="min-w-full">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">ID</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Source Path</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Project</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Experiment</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Time</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {events.map((e) => (
                      <tr key={e.id} className="hover:bg-gray-50">
                        <td className="px-4 py-3 text-sm">{e.id}</td>
                        <td className="px-4 py-3 text-sm font-mono truncate max-w-xs">{e.source_path}</td>
                        <td className="px-4 py-3">
                          <span className={`px-2 py-1 rounded text-xs font-medium ${statusColor(e.ingest_status)}`}>
                            {e.ingest_status}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-sm">{e.parsed_project_code || "—"}</td>
                        <td className="px-4 py-3 text-sm">{e.parsed_experiment_code || "—"}</td>
                        <td className="px-4 py-3 text-sm text-gray-500">
                          {new Date(e.created_at).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                    {events.length === 0 && (
                      <tr><td colSpan={6} className="px-4 py-8 text-center text-gray-500">No ingest events yet</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            ) : tab === "unclaimed" ? (
              <div className="space-y-3">
                {unclaimed.length === 0 ? (
                  <div className="text-center py-12 text-gray-500">No unclaimed entities</div>
                ) : (
                  unclaimed.map((u) => (
                    <div key={`${u.entity_type}-${u.entity_id}`} className="bg-white border rounded-lg p-4 flex items-center justify-between">
                      <div>
                        <span className={`px-2 py-1 rounded text-xs font-medium mr-2 ${
                          u.entity_type === "project" ? "bg-blue-100 text-blue-700" :
                          u.entity_type === "experiment" ? "bg-purple-100 text-purple-700" :
                          "bg-orange-100 text-orange-700"
                        }`}>{u.entity_type}</span>
                        <span className="font-medium">{u.name}</span>
                        <span className="text-sm text-gray-500 ml-2">ID: {u.entity_id}</span>
                        <span className="text-sm text-gray-400 ml-2">
                          Created {new Date(u.created_at).toLocaleDateString()}
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        {claimTarget?.entity_id === u.entity_id && claimTarget?.entity_type === u.entity_type ? (
                          <>
                            <input
                              value={claimEntityId}
                              onChange={(e) => setClaimEntityId(e.target.value)}
                              placeholder={`Target ${u.entity_type} ID`}
                              className="border rounded px-2 py-1 text-sm w-40"
                            />
                            <button
                              onClick={() => handleClaim(u)}
                              className="px-3 py-1 bg-bioaf-600 text-white rounded text-sm hover:bg-bioaf-700"
                            >
                              Confirm
                            </button>
                            <button
                              onClick={() => { setClaimTarget(null); setClaimEntityId(""); }}
                              className="px-3 py-1 border rounded text-sm text-gray-600"
                            >
                              Cancel
                            </button>
                          </>
                        ) : (
                          <button
                            onClick={() => setClaimTarget(u)}
                            className="px-3 py-1 border border-bioaf-600 text-bioaf-600 rounded text-sm hover:bg-bioaf-50"
                          >
                            Claim / Reassign
                          </button>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>
            ) : (
              <div className="bg-white border rounded-lg p-6">
                <h2 className="text-lg font-semibold mb-4">Simulate File Ingest</h2>
                <p className="text-sm text-gray-500 mb-4">
                  Test the ingest pipeline by simulating file arrivals. Files will be parsed against
                  active naming profiles and entities will be auto-created as needed.
                </p>
                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 mb-1">Source Bucket</label>
                  <input
                    value={simBucket}
                    onChange={(e) => setSimBucket(e.target.value)}
                    className="w-full border rounded-lg px-3 py-2"
                  />
                </div>
                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 mb-1">Filenames (one per line)</label>
                  <textarea
                    value={simFilenames}
                    onChange={(e) => setSimFilenames(e.target.value)}
                    placeholder="2026-03-10_ProjectX_RNASeq.fastq.gz&#10;2026-03-10_ProjectY_ChIPSeq.bam"
                    className="w-full h-32 border rounded-lg p-3 font-mono text-sm"
                  />
                </div>
                <button
                  onClick={handleSimulate}
                  className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700"
                >
                  Simulate Ingest
                </button>
                {simResults.length > 0 && (
                  <div className="mt-4">
                    <h3 className="text-sm font-medium text-gray-700 mb-2">Results</h3>
                    <div className="space-y-2">
                      {simResults.map((r) => (
                        <div key={r.id} className="flex items-center gap-2 text-sm">
                          <span className={`px-2 py-1 rounded text-xs font-medium ${statusColor(r.ingest_status)}`}>
                            {r.ingest_status}
                          </span>
                          <span className="font-mono">{r.source_path}</span>
                          {r.parsed_project_code && <span className="text-gray-500">Project: {r.parsed_project_code}</span>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
