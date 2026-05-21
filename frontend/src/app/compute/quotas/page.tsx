"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import { api } from "@/lib/api";
import type { UserQuota, BudgetInfo } from "@/lib/types";

export default function QuotasPage() {
  const router = useRouter();
  const [quotas, setQuotas] = useState<UserQuota[]>([]);
  const [budget, setBudget] = useState<BudgetInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [editingUser, setEditingUser] = useState<number | null>(null);
  const [editValue, setEditValue] = useState<string>("");
  const user = getCurrentUser();

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    loadData();
  }, [router]);

  async function loadData() {
    try {
      const [quotaData, budgetData] = await Promise.all([
        api.get<UserQuota[]>("/api/quotas"),
        api.get<BudgetInfo>("/api/compute/budget"),
      ]);
      setQuotas(quotaData);
      setBudget(budgetData);
    } catch {
    } finally {
      setLoading(false);
    }
  }

  async function handleSaveQuota(userId: number) {
    try {
      const limit = editValue === "" ? null : parseInt(editValue, 10);
      await api.patch(`/api/quotas/${userId}`, { cpu_hours_monthly_limit: limit });
      setEditingUser(null);
      loadData();
    } catch {}
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center gap-4 mb-6">
            <Link href="/infrastructure/compute" className="text-gray-500 hover:text-gray-700">← Cluster</Link>
            <h1 className="text-2xl font-bold">Cost Controls & Quotas</h1>
          </div>

          {loading ? (
            <div className="flex justify-center py-12"><LoadingSpinner size="lg" /></div>
          ) : (
            <div className="space-y-6">
              {/* Budget Overview */}
              {budget && (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div className="bg-white rounded-lg shadow p-6">
                    <p className="text-sm text-gray-500">Current Daily Spend</p>
                    <p className="text-2xl font-bold mt-1">${budget.current_spend.toFixed(2)}</p>
                  </div>
                  <div className="bg-white rounded-lg shadow p-6">
                    <p className="text-sm text-gray-500">Projected Monthly</p>
                    <p className="text-2xl font-bold mt-1">${budget.projected_spend.toFixed(2)}</p>
                  </div>
                  <div className="bg-white rounded-lg shadow p-6">
                    <p className="text-sm text-gray-500">Monthly Budget</p>
                    <p className="text-2xl font-bold mt-1">
                      {budget.monthly_budget != null ? `$${budget.monthly_budget.toFixed(2)}` : "Unlimited"}
                    </p>
                  </div>
                </div>
              )}

              {budget && budget.threshold_alerts.length > 0 && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                  {budget.threshold_alerts.map((alert, i) => (
                    <p key={i} className="text-red-700 text-sm">{alert}</p>
                  ))}
                </div>
              )}

              {/* User Quotas Table */}
              <div className="bg-white rounded-lg shadow">
                <div className="p-6 border-b">
                  <h2 className="text-lg font-semibold">User Quotas (CPU-Hours/Month)</h2>
                </div>
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">User</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Role</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Limit</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Used</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Progress</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Resets</th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {quotas.map((q) => {
                      const pct = q.cpu_hours_limit != null
                        ? Math.min(100, (q.cpu_hours_used / q.cpu_hours_limit) * 100)
                        : 0;
                      const isEditing = editingUser === q.user_id;

                      return (
                        <tr key={q.user_id} className="hover:bg-gray-50">
                          <td className="px-4 py-3 text-sm">
                            <div>{q.user_name || "—"}</div>
                            <div className="text-xs text-gray-500">{q.user_email}</div>
                          </td>
                          <td className="px-4 py-3 text-sm capitalize">{q.user_role || "—"}</td>
                          <td className="px-4 py-3 text-sm">
                            {isEditing ? (
                              <input
                                type="number"
                                value={editValue}
                                onChange={(e) => setEditValue(e.target.value)}
                                placeholder="Unlimited"
                                className="border rounded px-2 py-1 text-sm w-24"
                                autoFocus
                              />
                            ) : (
                              q.cpu_hours_limit != null ? `${q.cpu_hours_limit}h` : "Unlimited"
                            )}
                          </td>
                          <td className="px-4 py-3 text-sm">{q.cpu_hours_used.toFixed(1)}h</td>
                          <td className="px-4 py-3">
                            {q.cpu_hours_limit != null ? (
                              <div className="w-32">
                                <div className="bg-gray-200 rounded-full h-2">
                                  <div
                                    className={`h-2 rounded-full ${pct > 90 ? "bg-red-500" : pct > 70 ? "bg-yellow-500" : "bg-green-500"}`}
                                    style={{ width: `${pct}%` }}
                                  />
                                </div>
                                <p className="text-xs text-gray-500 mt-1">{pct.toFixed(0)}%</p>
                              </div>
                            ) : (
                              <span className="text-xs text-gray-400">N/A</span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-sm text-gray-500">
                            {new Date(q.quota_reset_at).toLocaleDateString()}
                          </td>
                          <td className="px-4 py-3">
                            {isEditing ? (
                              <div className="flex gap-2">
                                <button
                                  onClick={() => handleSaveQuota(q.user_id)}
                                  className="text-xs text-green-600 hover:text-green-800"
                                >
                                  Save
                                </button>
                                <button
                                  onClick={() => setEditingUser(null)}
                                  className="text-xs text-gray-500 hover:text-gray-700"
                                >
                                  Cancel
                                </button>
                              </div>
                            ) : (
                              <button
                                onClick={() => {
                                  setEditingUser(q.user_id);
                                  setEditValue(q.cpu_hours_limit?.toString() || "");
                                }}
                                className="text-xs text-bioaf-600 hover:text-bioaf-800"
                              >
                                Edit
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                    {quotas.length === 0 && (
                      <tr><td colSpan={7} className="px-4 py-8 text-center text-gray-400">No quotas configured</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
