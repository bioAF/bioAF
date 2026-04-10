"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import { api } from "@/lib/api";
import { ContentLoading } from "@/components/shared/ContentLoading";
import type { PipelineTrigger, BudgetStatus, PipelineRun } from "@/lib/types";

interface PipelineCatalogItem {
  id: number;
  pipeline_key: string;
  name: string;
}

export default function PipelineTriggersPage() {
  const router = useRouter();
  const [triggers, setTriggers] = useState<PipelineTrigger[]>([]);
  const [pipelines, setPipelines] = useState<PipelineCatalogItem[]>([]);
  const [budgetStatus, setBudgetStatus] = useState<BudgetStatus | null>(null);
  const [queuedRuns, setQueuedRuns] = useState<PipelineRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"triggers" | "queue" | "budget">("triggers");
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  // Form state
  const [formPipelineId, setFormPipelineId] = useState<number | "">("");
  const [formMode, setFormMode] = useState<"manual" | "event_driven" | "scheduled">("event_driven");
  const [formFileTypes, setFormFileTypes] = useState("fastq");
  const [formBatchWindow, setFormBatchWindow] = useState("15");
  const [formAutoQueue, setFormAutoQueue] = useState(true);
  const [formCronExpr, setFormCronExpr] = useState("0 6 * * 1");

  const user = getCurrentUser();
  const isAdmin = user?.role_name === "admin";

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    loadData();
  }, [router]);

  const loadData = async () => {
    try {
      const [trigs, cats, budget, queue] = await Promise.all([
        api.get<PipelineTrigger[]>("/api/pipeline-triggers"),
        api.get<PipelineCatalogItem[]>("/api/pipelines/catalog"),
        api.get<BudgetStatus>("/api/budget/status").catch(() => null),
        api.get<PipelineRun[]>("/api/pipeline-triggers/queue").catch(() => []),
      ]);
      setTriggers(trigs);
      setPipelines(cats);
      setBudgetStatus(budget);
      setQueuedRuns(queue);
    } catch {
      setError("Failed to load trigger data");
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async () => {
    setError("");
    setMessage("");
    if (!formPipelineId) { setError("Select a pipeline"); return; }

    try {
      const body: Record<string, unknown> = {
        pipeline_id: formPipelineId,
        trigger_mode: formMode,
        budget_config: { auto_queue_when_over_budget: formAutoQueue },
      };

      if (formMode === "event_driven") {
        body.event_config = {
          file_types: formFileTypes.split(",").map((s) => s.trim()).filter(Boolean),
          batching_window_minutes: parseInt(formBatchWindow) || 15,
        };
      } else if (formMode === "scheduled") {
        body.schedule_config = {
          cron_expression: formCronExpr,
        };
      }

      await api.post("/api/pipeline-triggers", body);
      setMessage("Trigger created");
      setShowCreate(false);
      await loadData();
    } catch {
      setError("Failed to create trigger");
    }
  };

  const handleToggle = async (trigger: PipelineTrigger) => {
    try {
      if (trigger.enabled) {
        await api.post(`/api/pipeline-triggers/${trigger.id}/disable`);
      } else {
        await api.put(`/api/pipeline-triggers/${trigger.id}`, { enabled: true });
      }
      await loadData();
    } catch {
      setError("Failed to update trigger");
    }
  };

  const handleApproveRun = async (runId: number) => {
    try {
      await api.post(`/api/pipeline-triggers/queue/${runId}/approve`);
      setMessage("Run approved");
      await loadData();
    } catch {
      setError("Failed to approve run");
    }
  };

  const getPipelineName = (pipelineId: number) => {
    const p = pipelines.find((cat) => cat.id === pipelineId);
    return p?.name || `Pipeline #${pipelineId}`;
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="max-w-6xl mx-auto">
            <div className="flex justify-between items-center mb-6">
              <h1 className="text-2xl font-bold text-gray-900">Pipeline Triggers <span className="ml-2 text-xs font-medium bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full align-middle">Beta</span></h1>
              <button
                onClick={() => setShowCreate(!showCreate)}
                className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700"
              >
                New Trigger
              </button>
            </div>

            {error && <div className="mb-4 p-3 bg-red-50 text-red-700 rounded-lg">{error}</div>}
            {message && <div className="mb-4 p-3 bg-green-50 text-green-700 rounded-lg">{message}</div>}

            {/* Budget Summary Card */}
            {budgetStatus && (
              <div className="mb-6 bg-white border rounded-lg p-4 flex items-center justify-between">
                <div>
                  <span className="text-sm text-gray-500">Monthly Budget</span>
                  <div className="text-lg font-semibold">${budgetStatus.monthly_budget.toFixed(2)}</div>
                </div>
                <div>
                  <span className="text-sm text-gray-500">Current Spend</span>
                  <div className="text-lg font-semibold">${budgetStatus.current_spend.toFixed(2)}</div>
                </div>
                <div>
                  <span className="text-sm text-gray-500">Remaining</span>
                  <div className={`text-lg font-semibold ${budgetStatus.remaining < 50 ? "text-red-600" : "text-green-600"}`}>
                    ${budgetStatus.remaining.toFixed(2)}
                  </div>
                </div>
                <div className="w-48">
                  <div className="flex justify-between text-xs text-gray-500 mb-1">
                    <span>Utilization</span>
                    <span>{budgetStatus.utilization_pct.toFixed(1)}%</span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-2">
                    <div
                      className={`h-2 rounded-full ${
                        budgetStatus.utilization_pct > 90 ? "bg-red-500" :
                        budgetStatus.utilization_pct > 70 ? "bg-yellow-500" : "bg-green-500"
                      }`}
                      style={{ width: `${Math.min(100, budgetStatus.utilization_pct)}%` }}
                    />
                  </div>
                </div>
              </div>
            )}

            {/* Tabs */}
            <div className="flex gap-1 mb-6 border-b">
              {(["triggers", "queue", "budget"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${
                    tab === t
                      ? "border-bioaf-600 text-bioaf-600"
                      : "border-transparent text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {t === "triggers" ? "Triggers" : t === "queue" ? `Budget Queue (${queuedRuns.length})` : "Budget Details"}
                </button>
              ))}
            </div>

            {/* Create Form */}
            {showCreate && (
              <div className="mb-6 bg-white border rounded-lg p-6">
                <h2 className="text-lg font-semibold mb-4">New Trigger</h2>
                <div className="grid grid-cols-2 gap-4 mb-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Pipeline</label>
                    <select
                      value={formPipelineId}
                      onChange={(e) => setFormPipelineId(parseInt(e.target.value) || "")}
                      className="w-full border rounded-lg px-3 py-2"
                    >
                      <option value="">Select pipeline...</option>
                      {pipelines.map((p) => (
                        <option key={p.id} value={p.id}>{p.name} ({p.pipeline_key})</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Trigger Mode</label>
                    <select
                      value={formMode}
                      onChange={(e) => setFormMode(e.target.value as typeof formMode)}
                      className="w-full border rounded-lg px-3 py-2"
                    >
                      <option value="manual">Manual</option>
                      <option value="event_driven">Event-Driven</option>
                      <option value="scheduled">Scheduled</option>
                    </select>
                  </div>
                </div>

                {formMode === "event_driven" && (
                  <div className="grid grid-cols-2 gap-4 mb-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">File Types (comma-separated)</label>
                      <input
                        value={formFileTypes}
                        onChange={(e) => setFormFileTypes(e.target.value)}
                        className="w-full border rounded-lg px-3 py-2"
                        placeholder="fastq, bam"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Batching Window (minutes)</label>
                      <input
                        type="number"
                        value={formBatchWindow}
                        onChange={(e) => setFormBatchWindow(e.target.value)}
                        className="w-full border rounded-lg px-3 py-2"
                        min="0"
                      />
                    </div>
                  </div>
                )}

                {formMode === "scheduled" && (
                  <div className="mb-4">
                    <label className="block text-sm font-medium text-gray-700 mb-1">Cron Expression</label>
                    <input
                      value={formCronExpr}
                      onChange={(e) => setFormCronExpr(e.target.value)}
                      className="w-full border rounded-lg px-3 py-2 font-mono"
                      placeholder="0 6 * * 1"
                    />
                    <p className="text-xs text-gray-500 mt-1">Example: &quot;0 6 * * 1&quot; = Every Monday at 6 AM</p>
                  </div>
                )}

                <div className="flex items-center gap-2 mb-4">
                  <input
                    type="checkbox"
                    checked={formAutoQueue}
                    onChange={(e) => setFormAutoQueue(e.target.checked)}
                    id="auto-queue"
                  />
                  <label htmlFor="auto-queue" className="text-sm text-gray-700">
                    Auto-queue runs when over budget (instead of skipping)
                  </label>
                </div>

                <div className="flex gap-2">
                  <button
                    onClick={handleCreate}
                    className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700"
                  >
                    Create Trigger
                  </button>
                  <button
                    onClick={() => setShowCreate(false)}
                    className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {loading ? (
              <ContentLoading />
            ) : tab === "triggers" ? (
              <div className="bg-white border rounded-lg overflow-hidden">
                <table className="min-w-full">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Pipeline</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Mode</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Config</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                      <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {triggers.map((t) => (
                      <tr key={t.id} className="hover:bg-gray-50">
                        <td className="px-6 py-4 font-medium">{getPipelineName(t.pipeline_id)}</td>
                        <td className="px-6 py-4">
                          <span className={`px-2 py-1 rounded text-xs font-medium ${
                            t.trigger_mode === "event_driven" ? "bg-blue-100 text-blue-700" :
                            t.trigger_mode === "scheduled" ? "bg-purple-100 text-purple-700" :
                            "bg-gray-100 text-gray-600"
                          }`}>{t.trigger_mode}</span>
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-500">
                          {t.trigger_mode === "event_driven" && t.event_config && (
                            <span>
                              Files: {(t.event_config.file_types as string[])?.join(", ") || "any"}
                              {t.event_config.batching_window_minutes ? ` | Window: ${t.event_config.batching_window_minutes}m` : ""}
                            </span>
                          )}
                          {t.trigger_mode === "scheduled" && t.schedule_config && (
                            <span className="font-mono">{t.schedule_config.cron_expression as string}</span>
                          )}
                          {t.trigger_mode === "manual" && "Manual trigger only"}
                        </td>
                        <td className="px-6 py-4">
                          <span className={`px-2 py-1 rounded text-xs font-medium ${
                            t.enabled ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-600"
                          }`}>{t.enabled ? "Active" : "Disabled"}</span>
                        </td>
                        <td className="px-6 py-4 text-right">
                          <button
                            onClick={() => handleToggle(t)}
                            className={`text-sm ${t.enabled ? "text-red-600 hover:text-red-700" : "text-green-600 hover:text-green-700"}`}
                          >
                            {t.enabled ? "Disable" : "Enable"}
                          </button>
                        </td>
                      </tr>
                    ))}
                    {triggers.length === 0 && (
                      <tr><td colSpan={5} className="px-6 py-8 text-center text-gray-500">No triggers configured</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            ) : tab === "queue" ? (
              <div className="space-y-3">
                {queuedRuns.length === 0 ? (
                  <div className="text-center py-12 text-gray-500">No runs in budget queue</div>
                ) : (
                  queuedRuns.map((run) => (
                    <div key={run.id} className="bg-white border rounded-lg p-4 flex items-center justify-between">
                      <div>
                        <span className="font-medium">{run.pipeline_name}</span>
                        <span className="text-sm text-gray-500 ml-2">Run #{run.id}</span>
                        {run.cost_estimate && (
                          <span className="ml-2 text-sm text-orange-600">
                            ~${Number(run.cost_estimate).toFixed(2)}/hr
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="px-2 py-1 bg-yellow-100 text-yellow-700 rounded text-xs font-medium">
                          pending_budget_review
                        </span>
                        {isAdmin && (
                          <button
                            onClick={() => handleApproveRun(run.id)}
                            className="px-3 py-1 bg-green-600 text-white rounded text-sm hover:bg-green-700"
                          >
                            Approve
                          </button>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>
            ) : (
              <div className="bg-white border rounded-lg p-6">
                <h2 className="text-lg font-semibold mb-4">Budget Details</h2>
                {budgetStatus ? (
                  <div className="grid grid-cols-2 gap-6">
                    <div>
                      <h3 className="text-sm font-medium text-gray-500 mb-2">Monthly Allocation</h3>
                      <p className="text-3xl font-bold">${budgetStatus.monthly_budget.toFixed(2)}</p>
                    </div>
                    <div>
                      <h3 className="text-sm font-medium text-gray-500 mb-2">Spent This Month</h3>
                      <p className="text-3xl font-bold">${budgetStatus.current_spend.toFixed(2)}</p>
                    </div>
                    <div>
                      <h3 className="text-sm font-medium text-gray-500 mb-2">Remaining Budget</h3>
                      <p className={`text-3xl font-bold ${budgetStatus.remaining < 50 ? "text-red-600" : "text-green-600"}`}>
                        ${budgetStatus.remaining.toFixed(2)}
                      </p>
                    </div>
                    <div>
                      <h3 className="text-sm font-medium text-gray-500 mb-2">Utilization</h3>
                      <p className="text-3xl font-bold">{budgetStatus.utilization_pct.toFixed(1)}%</p>
                    </div>
                  </div>
                ) : (
                  <p className="text-gray-500">Budget data unavailable. Configure BIOAF_MONTHLY_BUDGET environment variable.</p>
                )}
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
