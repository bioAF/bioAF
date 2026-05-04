import { formatMetric, evaluateThreshold, type MetricStatus } from "./format";
import type { QCMetricSpec } from "@/lib/types";

function bgFor(status: MetricStatus): string {
  switch (status) {
    case "good": return "bg-green-50 border-green-200";
    case "warn": return "bg-yellow-50 border-yellow-200";
    case "bad":  return "bg-red-50 border-red-200";
    default:     return "bg-gray-50 border-gray-200";
  }
}

function fgFor(status: MetricStatus): string {
  switch (status) {
    case "good": return "text-green-700";
    case "warn": return "text-yellow-700";
    case "bad":  return "text-red-700";
    default:     return "text-gray-900";
  }
}

export function MetricCard({ value, spec }: { value: unknown; spec: QCMetricSpec }) {
  const status = evaluateThreshold(value, spec);
  return (
    <div className={`rounded-lg border p-3 ${bgFor(status)}`}>
      <p className="text-xs text-gray-500">{spec.label}</p>
      <p className={`text-lg font-semibold ${fgFor(status)}`}>{formatMetric(value, spec)}</p>
    </div>
  );
}

export function HeroMetric({ value, spec }: { value: unknown; spec: QCMetricSpec }) {
  const status = evaluateThreshold(value, spec);
  return (
    <div className="text-center">
      <p className="text-sm text-gray-500 mb-1">{spec.label}</p>
      <p className={`text-3xl font-bold ${fgFor(status)}`}>{formatMetric(value, spec)}</p>
    </div>
  );
}
