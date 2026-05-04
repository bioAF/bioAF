/**
 * Generic metric formatting + threshold evaluation driven by qc_config.
 *
 * Formats supported: integer, decimal, percent_decimal (0.85 -> "85.0%"),
 * percent_pct (already a %), bp, raw.
 */

import type { QCMetricSpec } from "@/lib/types";

export type MetricStatus = "good" | "warn" | "bad" | "neutral";

export function formatMetric(value: unknown, spec: QCMetricSpec | undefined): string {
  if (value == null) return "—";
  const fmt = spec?.format ?? "raw";
  if (typeof value !== "number") return String(value);

  switch (fmt) {
    case "integer":
      return Math.round(value).toLocaleString();
    case "decimal":
      return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
    case "percent_decimal":
      return `${(value * 100).toFixed(1)}%`;
    case "percent_pct":
      return `${value.toFixed(1)}%`;
    case "bp":
      return `${Math.round(value).toLocaleString()} bp`;
    case "raw":
    default:
      return value.toLocaleString();
  }
}

/** "operator value" -> evaluator that returns true when val passes the rule.
 *  Supports: >=, <=, >, <, ==. */
function compile(rule: string): (val: number) => boolean {
  const m = rule.match(/^\s*(>=|<=|>|<|==)\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (!m) return () => false;
  const op = m[1];
  const target = parseFloat(m[2]);
  switch (op) {
    case ">=": return (v) => v >= target;
    case "<=": return (v) => v <= target;
    case ">":  return (v) => v > target;
    case "<":  return (v) => v < target;
    case "==": return (v) => v === target;
    default:   return () => false;
  }
}

export function evaluateThreshold(value: unknown, spec: QCMetricSpec | undefined): MetricStatus {
  if (value == null || typeof value !== "number") return "neutral";
  const t = spec?.thresholds;
  if (!t) return "neutral";
  if (t.good && compile(t.good)(value)) return "good";
  if (t.warn && compile(t.warn)(value)) return "warn";
  return "bad";
}

export function getNested(obj: Record<string, unknown>, path: string): unknown {
  return path.split(".").reduce<unknown>((acc, key) => {
    if (acc != null && typeof acc === "object") return (acc as Record<string, unknown>)[key];
    return undefined;
  }, obj);
}
