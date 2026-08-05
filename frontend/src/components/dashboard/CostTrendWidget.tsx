"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

interface DailyCost {
  date: string;
  amount: number | string;
}

interface CostHistory {
  records: DailyCost[];
  total_amount: number | string;
}

function num(value: number | string): number {
  const n = Number(value);
  return Number.isNaN(n) ? 0 : n;
}

function dateStr(offsetDays: number): string {
  return new Date(Date.now() - offsetDays * 86400000).toISOString().slice(0, 10);
}

export function CostTrendWidget() {
  const [data, setData] = useState<CostHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timeout = setTimeout(() => setLoading(false), 60000);
    api
      .getWithRetry<CostHistory>(
        `/api/costs/history?start_date=${dateStr(29)}&end_date=${dateStr(0)}`,
      )
      .then((res) => setData({ records: res.records || [], total_amount: res.total_amount ?? 0 }))
      .catch(() => setError("Failed to load cost trend"))
      .finally(() => {
        clearTimeout(timeout);
        setLoading(false);
      });
    return () => clearTimeout(timeout);
  }, []);

  const records = data?.records ?? [];
  const max = records.reduce((m, r) => Math.max(m, num(r.amount)), 0);

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-cost-trend">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Cost / spend trend
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-500 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" />
          <span className="text-sm">Loading trend...</span>
        </div>
      )}
      {error && !loading && (
        <div className="text-sm text-red-600" data-testid="widget-error">
          {error}
          <button
            onClick={() => window.location.reload()}
            className="ml-2 text-bioaf-600 hover:underline"
          >
            Retry
          </button>
        </div>
      )}
      {!loading && !error && records.length === 0 && (
        <p className="text-sm text-gray-500" data-testid="widget-empty">
          No cost history yet.
        </p>
      )}
      {!loading && !error && records.length > 0 && data && (
        <div>
          <div className="text-2xl font-bold text-gray-800">
            ${num(data.total_amount).toFixed(2)}
          </div>
          <p className="text-xs text-gray-500 mb-2">last 30 days</p>
          <div className="flex items-end gap-0.5 h-16" data-testid="cost-trend-chart">
            {records.map((r, i) => (
              <div
                key={i}
                title={`${r.date}: $${num(r.amount).toFixed(2)}`}
                className="flex-1 bg-bioaf-400 rounded-sm"
                style={{ height: `${max > 0 ? Math.max((num(r.amount) / max) * 100, 2) : 0}%` }}
              />
            ))}
          </div>
          <Link
            href="/infrastructure/cost-center"
            className="text-xs text-bioaf-600 hover:underline mt-2 inline-block"
          >
            View cost center
          </Link>
        </div>
      )}
    </div>
  );
}
