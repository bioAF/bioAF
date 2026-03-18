"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

interface ComponentCost {
  component: string;
  amount: string;
  percentage: number;
}

interface CostSummaryResponse {
  current_month_spend: number;
  monthly_budget: number | null;
  budget_remaining: number | null;
  projected_month_end: number | null;
  breakdown_by_component: ComponentCost[];
  currency?: string;
}

interface CostData {
  current_spend: number;
  monthly_budget: number | null;
  breakdown: ComponentCost[];
  currency: string;
}

const COMPONENT_LABELS: Record<string, string> = {
  node: "bioAF Node",
  storage: "Storage",
  compute: "Compute",
  other: "Other Services",
};

const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: "$",
  EUR: "\u20AC",
  GBP: "\u00A3",
  JPY: "\u00A5",
  CHF: "CHF\u00A0",
  CAD: "CA$",
  AUD: "A$",
  NZD: "NZ$",
  HKD: "HK$",
  SGD: "S$",
  SEK: "kr\u00A0",
  NOK: "kr\u00A0",
  DKK: "kr\u00A0",
  INR: "\u20B9",
  KRW: "\u20A9",
  BRL: "R$",
  MXN: "MX$",
  PLN: "z\u0142\u00A0",
  TWD: "NT$",
  THB: "\u0E3F",
  TRY: "\u20BA",
  ILS: "\u20AA",
  MYR: "RM\u00A0",
  CZK: "K\u010D\u00A0",
  CLP: "CLP\u00A0",
  COP: "COP\u00A0",
  PEN: "S/",
  IDR: "Rp\u00A0",
  VND: "\u20AB",
};

function formatCurrency(value: number | string, currency: string): string {
  const symbol = CURRENCY_SYMBOLS[currency] || `${currency}\u00A0`;
  return `${symbol}${Number(value).toFixed(2)}`;
}

export function CostBudgetWidget() {
  const [data, setData] = useState<CostData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<CostSummaryResponse>("/api/costs/summary")
      .then((res) => {
        setData({
          current_spend: res.current_month_spend,
          monthly_budget: res.monthly_budget,
          breakdown: res.breakdown_by_component || [],
          currency: res.currency || "USD",
        });
      })
      .catch(() => setError("Failed to load budget data"))
      .finally(() => setLoading(false));
  }, []);

  const hasBudget = data?.monthly_budget != null;
  const pct = hasBudget
    ? Math.min((Number(data!.current_spend) / Number(data!.monthly_budget!)) * 100, 100)
    : 0;
  const barColor = pct > 90 ? "bg-red-500" : pct > 75 ? "bg-amber-500" : "bg-green-500";
  const fmt = (value: number | string) => formatCurrency(value, data?.currency || "USD");

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-cost-budget">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Cost vs. Budget
      </h3>
      {loading && (
        <div className="animate-pulse space-y-2" data-testid="widget-loading">
          <div className="h-4 bg-gray-100 rounded w-1/2" />
          <div className="h-3 bg-gray-100 rounded" />
        </div>
      )}
      {error && (
        <div className="text-sm text-red-600" data-testid="widget-error">
          {error}
          <div className="mt-2 flex gap-2">
            <button onClick={() => window.location.reload()} className="text-bioaf-600 hover:underline">
              Retry
            </button>
            <Link href="/infrastructure/cost-center" className="text-bioaf-600 hover:underline">
              Set up billing
            </Link>
          </div>
        </div>
      )}
      {!loading && !error && data && (
        <div>
          <div className="flex justify-between text-sm mb-1">
            <span className="text-gray-700">
              {fmt(data.current_spend)}
            </span>
            <span className="text-gray-500">
              {hasBudget ? fmt(data.monthly_budget!) : "\u221E"}
            </span>
          </div>
          {hasBudget ? (
            <>
              <div className="w-full h-3 bg-gray-200 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full ${barColor}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <p className="text-xs text-gray-500 mt-1">{Math.round(pct)}% of monthly budget</p>
            </>
          ) : (
            <p className="text-xs text-gray-400 mt-1" data-testid="widget-no-budget">
              No budget set.{" "}
              <Link href="/infrastructure/cost-center" className="text-bioaf-600 hover:underline">
                Configure in Cost Center
              </Link>
            </p>
          )}

          {data.breakdown.length > 0 && (
            <div className="mt-3 space-y-1 border-t pt-2">
              {data.breakdown.map((item) => (
                <div key={item.component} className="flex justify-between text-xs text-gray-600">
                  <span>{COMPONENT_LABELS[item.component] || item.component}</span>
                  <span>{fmt(item.amount)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
