"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import { api } from "@/lib/api";
import { BillingSetupModal } from "@/components/infrastructure/BillingSetupModal";
import { TerraformProgressModal } from "@/components/infrastructure/TerraformProgressModal";

interface BillingExportStatus {
  configured: boolean;
  dataset_id: string;
  console_url: string;
  table_id: string;
}

interface DailyCost {
  date: string;
  amount: string;
}

interface ComponentCost {
  component: string;
  amount: string;
  percentage: number;
}

interface CostSummary {
  current_month_spend: string;
  daily_trend: DailyCost[];
  breakdown_by_component: ComponentCost[];
  monthly_budget: string | null;
  budget_remaining: string | null;
  projected_month_end: string | null;
  currency: string;
}

interface BudgetConfig {
  monthly_budget: string | null;
  threshold_50_enabled: boolean;
  threshold_80_enabled: boolean;
  threshold_100_enabled: boolean;
  scale_to_zero_on_100: boolean;
  currency: string;
}

const SUPPORTED_CURRENCIES = [
  "AUD", "BRL", "CAD", "CHF", "CLP", "COP", "CZK", "DKK",
  "EUR", "GBP", "HKD", "IDR", "ILS", "INR", "JPY", "KRW",
  "MXN", "MYR", "NOK", "NZD", "PEN", "PLN", "SEK", "SGD",
  "THB", "TRY", "TWD", "USD", "VND",
] as const;

const COMPONENT_LABELS: Record<string, string> = {
  node: "bioAF Node",
  storage: "Storage",
  compute: "Compute",
  other: "Other Services",
};

export default function InfraCostCenterPage() {
  const router = useRouter();
  const [summary, setSummary] = useState<CostSummary | null>(null);
  const [budget, setBudget] = useState<BudgetConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [budgetInput, setBudgetInput] = useState("");
  const [currencyInput, setCurrencyInput] = useState("USD");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [billingExport, setBillingExport] = useState<BillingExportStatus | null>(null);
  const [showBillingSetupModal, setShowBillingSetupModal] = useState(false);
  const [showTeardownModal, setShowTeardownModal] = useState(false);
  const [showTeardownConfirm, setShowTeardownConfirm] = useState(false);

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    const user = getCurrentUser();
    if (user?.role !== "admin") { router.push("/"); return; }

    const load = async () => {
      try {
        const [s, b] = await Promise.all([
          api.get<CostSummary>("/api/costs/summary"),
          api.get<BudgetConfig>("/api/costs/budget"),
        ]);
        setSummary(s);
        setBudget(b);
        setBudgetInput(b.monthly_budget || "");
        setCurrencyInput(b.currency || "USD");
      } catch {
        // ignore
      }
      try {
        const be = await api.get<BillingExportStatus>(
          "/api/v1/infrastructure/billing-export/status",
        );
        setBillingExport(be);
      } catch {
        // ignore -- endpoint may not exist in older versions
      }
      setLoading(false);
    };
    load();
  }, [router]);

  const currency = budget?.currency || "USD";

  const handleSaveBudget = async () => {
    if (!budget) return;
    setSaving(true);
    setMessage("");
    try {
      const updated = await api.put<BudgetConfig>("/api/costs/budget", {
        monthly_budget: budgetInput || null,
        threshold_50_enabled: budget.threshold_50_enabled,
        threshold_80_enabled: budget.threshold_80_enabled,
        threshold_100_enabled: budget.threshold_100_enabled,
        scale_to_zero_on_100: budget.scale_to_zero_on_100,
        currency: currencyInput,
      });
      setBudget(updated);
      setMessage("Budget configuration saved");
    } catch {
      setMessage("Failed to save budget configuration");
    } finally {
      setSaving(false);
    }
  };

  const budgetPct = summary && budget?.monthly_budget
    ? Math.min(100, (parseFloat(summary.current_month_spend) / parseFloat(budget.monthly_budget)) * 100)
    : 0;

  const fmt = (value: string | number) => `${parseFloat(String(value)).toFixed(2)} ${currency}`;

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold text-gray-900 mb-6">Cost Center</h1>

          {billingExport && !billingExport.configured && !billingExport.dataset_id && (
            <div className="mb-4 p-4 rounded-lg border border-amber-200 bg-amber-50 flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-amber-800">Cost data is estimated</p>
                <p className="text-xs text-amber-600 mt-0.5">
                  Set up BigQuery billing export for accurate, invoice-matched costs.
                </p>
              </div>
              <button
                onClick={() => setShowBillingSetupModal(true)}
                className="px-3 py-1.5 bg-amber-600 text-white rounded text-sm font-medium hover:bg-amber-700 whitespace-nowrap"
              >
                Set Up Billing Export
              </button>
            </div>
          )}

          {billingExport && !billingExport.configured && billingExport.dataset_id && (
            <div className="mb-4 p-4 rounded-lg border border-blue-200 bg-blue-50 flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-blue-800">Waiting for billing export data</p>
                <p className="text-xs text-blue-600 mt-0.5">
                  Dataset created. Data typically appears within 24 hours after enabling export in the Google Cloud Console.
                </p>
              </div>
              <button
                onClick={() => setShowBillingSetupModal(true)}
                className="px-3 py-1.5 bg-blue-600 text-white rounded text-sm font-medium hover:bg-blue-700 whitespace-nowrap"
              >
                Check Status
              </button>
            </div>
          )}

          {billingExport?.configured && (
            <div className="mb-4 flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs text-green-700">
                <span className="inline-block h-2 w-2 rounded-full bg-green-500" />
                Using BigQuery billing export
              </div>
              <button
                onClick={() => setShowTeardownConfirm(true)}
                className="px-3 py-1.5 bg-red-50 text-red-700 border border-red-200 rounded text-xs font-medium hover:bg-red-100"
              >
                Teardown Billing Export
              </button>
            </div>
          )}

          {billingExport && !billingExport.configured && billingExport.dataset_id && (
            <div className="mb-1 flex justify-end">
              <button
                onClick={() => setShowTeardownConfirm(true)}
                className="px-3 py-1.5 bg-red-50 text-red-700 border border-red-200 rounded text-xs font-medium hover:bg-red-100"
              >
                Teardown Billing Export
              </button>
            </div>
          )}

          {showBillingSetupModal && (
            <BillingSetupModal
              datasetExists={!!billingExport?.dataset_id}
              consoleUrl={billingExport?.console_url}
              onComplete={() => {
                setShowBillingSetupModal(false);
                // Refresh data
                api.get<BillingExportStatus>("/api/v1/infrastructure/billing-export/status")
                  .then(setBillingExport)
                  .catch(() => {});
                api.get<CostSummary>("/api/costs/summary")
                  .then(setSummary)
                  .catch(() => {});
              }}
              onClose={() => setShowBillingSetupModal(false)}
            />
          )}

          {showTeardownConfirm && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
              <div className="bg-white rounded-xl shadow-xl w-full max-w-md mx-4 p-6">
                <h2 className="text-lg font-semibold text-gray-900 mb-3">Teardown Billing Export</h2>
                <p className="text-sm text-gray-600 mb-2">
                  This will destroy the BigQuery billing export dataset and all associated IAM bindings via Terraform.
                </p>
                <p className="text-sm text-gray-600 mb-4">
                  You can re-create it afterward using the setup flow. Any billing export configuration in the Google Cloud Console will need to be re-pointed to the new dataset.
                </p>
                <div className="flex justify-end gap-2">
                  <button
                    onClick={() => setShowTeardownConfirm(false)}
                    className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-300"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => {
                      setShowTeardownConfirm(false);
                      setShowTeardownModal(true);
                    }}
                    className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm font-medium hover:bg-red-700"
                  >
                    Confirm Teardown
                  </button>
                </div>
              </div>
            </div>
          )}

          {showTeardownModal && (
            <TerraformProgressModal
              title="Teardown Billing Export"
              sseUrl="/api/v1/infrastructure/billing-export/teardown"
              mode="teardown"
              onComplete={() => {
                setShowTeardownModal(false);
                setBillingExport(null);
                // Refresh status
                api.get<BillingExportStatus>("/api/v1/infrastructure/billing-export/status")
                  .then(setBillingExport)
                  .catch(() => {});
                api.get<CostSummary>("/api/costs/summary")
                  .then(setSummary)
                  .catch(() => {});
              }}
              onClose={() => setShowTeardownModal(false)}
            />
          )}

          {message && (
            <div className="mb-4 p-3 rounded bg-green-50 text-green-700 text-sm">{message}</div>
          )}

          {loading ? (
            <div className="text-gray-500">Loading cost data...</div>
          ) : summary && (
            <>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                <div className="bg-white rounded-lg border border-gray-200 p-4">
                  <p className="text-sm text-gray-600">Current Month Spend</p>
                  <p className="text-2xl font-bold text-gray-900">{fmt(summary.current_month_spend)}</p>
                </div>
                <div className="bg-white rounded-lg border border-gray-200 p-4">
                  <p className="text-sm text-gray-600">Budget Remaining</p>
                  <p className="text-2xl font-bold text-gray-900">
                    {summary.budget_remaining ? fmt(summary.budget_remaining) : "No budget set"}
                  </p>
                </div>
                <div className="bg-white rounded-lg border border-gray-200 p-4">
                  <p className="text-sm text-gray-600">Projected Month End</p>
                  <p className="text-2xl font-bold text-gray-900">
                    {summary.projected_month_end ? fmt(summary.projected_month_end) : "N/A"}
                  </p>
                </div>
              </div>

              {budget?.monthly_budget && (
                <div className="bg-white rounded-lg border border-gray-200 p-4 mb-6">
                  <div className="flex justify-between text-sm mb-2">
                    <span className="text-gray-600">Budget Usage</span>
                    <span className="font-medium">{budgetPct.toFixed(1)}%</span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-4 relative">
                    <div
                      className={`h-4 rounded-full ${
                        budgetPct >= 100 ? "bg-red-500" : budgetPct >= 80 ? "bg-yellow-500" : "bg-green-500"
                      }`}
                      style={{ width: `${Math.min(100, budgetPct)}%` }}
                    />
                    <div className="absolute top-0 left-1/2 w-0.5 h-4 bg-yellow-600 opacity-50" />
                    <div className="absolute top-0 left-[80%] w-0.5 h-4 bg-orange-600 opacity-50" />
                  </div>
                </div>
              )}

              <div className="bg-white rounded-lg border border-gray-200 p-4 mb-6">
                <h2 className="font-semibold text-gray-900 mb-3">Breakdown by Component</h2>
                {summary.breakdown_by_component.length === 0 ? (
                  <p className="text-gray-500 text-sm">No cost data for this month</p>
                ) : (
                  <div className="space-y-2">
                    {summary.breakdown_by_component.map((c) => (
                      <div key={c.component} className="flex items-center gap-3">
                        <span className="text-sm w-32 text-gray-700">{COMPONENT_LABELS[c.component] || c.component}</span>
                        <div className="flex-1 bg-gray-100 rounded-full h-3">
                          <div
                            className="bg-bioaf-500 h-3 rounded-full"
                            style={{ width: `${c.percentage}%` }}
                          />
                        </div>
                        <span className="text-sm text-gray-900 w-32 text-right">
                          {fmt(c.amount)} ({c.percentage}%)
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {summary.daily_trend.length > 0 && (
                <div className="bg-white rounded-lg border border-gray-200 p-4 mb-6">
                  <h2 className="font-semibold text-gray-900 mb-3">Daily Trend</h2>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b bg-gray-50">
                          <th className="text-left px-3 py-2 font-medium text-gray-700">Date</th>
                          <th className="text-right px-3 py-2 font-medium text-gray-700">Amount</th>
                        </tr>
                      </thead>
                      <tbody>
                        {summary.daily_trend.map((d) => (
                          <tr key={d.date} className="border-b">
                            <td className="px-3 py-2 text-gray-900">{d.date}</td>
                            <td className="px-3 py-2 text-right text-gray-900">{fmt(d.amount)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {budget && (
                <div className="bg-white rounded-lg border border-gray-200 p-4">
                  <h2 className="font-semibold text-gray-900 mb-4">Budget Configuration</h2>
                  <div className="space-y-4 max-w-md">
                    <div>
                      <label className="block text-sm text-gray-700 mb-1">Currency</label>
                      <select
                        value={currencyInput}
                        onChange={(e) => setCurrencyInput(e.target.value)}
                        className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                        data-testid="currency-select"
                      >
                        {SUPPORTED_CURRENCIES.map((c) => (
                          <option key={c} value={c}>{c}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="block text-sm text-gray-700 mb-1">Monthly Budget ({currencyInput})</label>
                      <input
                        type="number"
                        value={budgetInput}
                        onChange={(e) => setBudgetInput(e.target.value)}
                        className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                        placeholder="e.g. 5000"
                      />
                    </div>
                    <div className="space-y-2">
                      {[
                        { key: "threshold_50_enabled", label: "Alert at 50% budget" },
                        { key: "threshold_80_enabled", label: "Alert at 80% budget" },
                        { key: "threshold_100_enabled", label: "Alert at 100% budget" },
                        { key: "scale_to_zero_on_100", label: "Scale to zero at 100% (stops compute)" },
                      ].map(({ key, label }) => (
                        <label key={key} className="flex items-center gap-2 text-sm">
                          <input
                            type="checkbox"
                            checked={budget[key as keyof BudgetConfig] as boolean}
                            onChange={(e) =>
                              setBudget({ ...budget, [key]: e.target.checked })
                            }
                            className="rounded border-gray-300"
                          />
                          <span className="text-gray-700">{label}</span>
                        </label>
                      ))}
                    </div>
                    <button
                      onClick={handleSaveBudget}
                      disabled={saving}
                      className="bg-bioaf-600 text-white px-4 py-2 rounded hover:bg-bioaf-700 disabled:opacity-50"
                    >
                      {saving ? "Saving..." : "Save Budget Config"}
                    </button>
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
