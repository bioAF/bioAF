"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useWidgetData } from "@/hooks/useWidgetData";
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

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * Read a calendar day off the string rather than through a Date.
 *
 * `new Date("2026-08-01")` is parsed as UTC midnight, so in any negative-offset
 * timezone -- the whole of the Americas -- formatting it renders Jul 31. These
 * are plain calendar days from a SQL date column, with no time and no zone.
 */
function dayOf(iso: string): string {
  return String(Number(iso.slice(8, 10)));
}

function shortDate(iso: string): string {
  const month = MONTHS[Number(iso.slice(5, 7)) - 1] ?? iso.slice(5, 7);
  return `${month} ${dayOf(iso)}`;
}

/**
 * Label every Nth bar, counting back from the most recent.
 *
 * Measured on the deployed widget: the chart is 328px wide at a 1440px viewport
 * but only 189px at 1024px, where a full 30-day window leaves each bar about
 * 4.4px. A two-digit label needs roughly 14px, so labelling every bar at 30 days
 * would overlap them into mush. Stepping is by bar count rather than measured
 * width on purpose: it needs no ResizeObserver, renders the same on the server
 * and the client, and is a number a test can assert.
 */
function labelStep(count: number): number {
  if (count <= 12) return 1;
  if (count <= 20) return 2;
  return 3;
}

/**
 * Centre the readout over its bar, except near the ends, where centring would
 * push half of it outside the widget.
 */
function tooltipPosition(index: number, count: number): React.CSSProperties {
  const centre = count > 0 ? ((index + 0.5) / count) * 100 : 50;
  if (centre < 25) return { left: 0 };
  if (centre > 75) return { right: 0 };
  return { left: `${centre}%`, transform: "translate(-50%, -100%)" };
}

export function CostTrendWidget() {
  const { data, loading, error, retry } = useWidgetData(
    async () => {
      const res = await api.getWithRetry<CostHistory>(`/api/costs/history?start_date=${dateStr(29)}&end_date=${dateStr(0)}`,);
      return { records: res.records || [], total_amount: res.total_amount ?? 0 };
    },
    "Cost trend",
  );

  const records = data?.records ?? [];
  const max = records.reduce((m, r) => Math.max(m, num(r.amount)), 0);
  const [active, setActive] = useState<number | null>(null);
  const step = labelStep(records.length);
  const hovered = active !== null ? records[active] : undefined;

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
            onClick={retry}
            className="ml-2 text-bioaf-600 hover:underline"
          >
            Retry
          </button>
        </div>
      )}
      {!loading && !error && records.length === 0 && (
        <div data-testid="widget-empty">
          {/* This asked about 30 days, so it cannot say none has ever been recorded. */}
          <p className="text-sm text-gray-500">No cost recorded in the last 30 days.</p>
          <Link
            href="/infrastructure/cost-center"
            className="text-xs text-bioaf-600 hover:underline mt-2 inline-block"
          >
            View cost center
          </Link>
        </div>
      )}
      {!loading && !error && records.length > 0 && data && (
        <div>
          <div className="text-2xl font-bold text-gray-800">
            ${num(data.total_amount).toFixed(2)}
          </div>
          <p className="text-xs text-gray-500 mb-2">last 30 days</p>

          <div className="relative">
            {/* Anchored over the hovered bar, and clamped at the ends so it does
                not hang off a widget that is only 189px wide at 1024px. */}
            {hovered && active !== null && (
              <div
                data-testid="cost-trend-tooltip"
                // No live region: this duplicates the table below, and
                // announcing both would say every figure twice.
                aria-hidden="true"
                className="absolute -top-1 z-10 -translate-y-full whitespace-nowrap rounded bg-gray-800 px-2 py-1 text-xs text-white shadow"
                style={tooltipPosition(active, records.length)}
              >
                <span className="font-medium">{shortDate(hovered.date)}</span>
                <span className="ml-2">${num(hovered.amount).toFixed(2)}</span>
              </div>
            )}

            {/* ONE tab stop for the whole chart, with arrows to walk the days.
                A tabIndex per bar would put 30 stops in the middle of the
                dashboard for a control that shows a number. Touch gets
                onTouchStart, because a tap is the only pointer a phone has. */}
            <div
              className="flex items-end gap-0.5 h-16 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-bioaf-600"
              data-testid="cost-trend-chart"
              tabIndex={0}
              role="group"
              aria-label="Daily cost chart. Use the arrow keys to read each day."
              onMouseLeave={() => setActive(null)}
              onBlur={() => setActive(null)}
              onKeyDown={(e) => {
                if (records.length === 0) return;
                const last = records.length - 1;
                if (e.key === "ArrowRight") {
                  setActive((a) => (a === null ? 0 : Math.min(a + 1, last)));
                } else if (e.key === "ArrowLeft") {
                  setActive((a) => (a === null ? last : Math.max(a - 1, 0)));
                } else if (e.key === "Home") {
                  setActive(0);
                } else if (e.key === "End") {
                  setActive(last);
                } else if (e.key === "Escape") {
                  setActive(null);
                } else {
                  return;
                }
                e.preventDefault();
              }}
            >
              {records.map((r, i) => (
                <div
                  key={i}
                  data-testid="cost-trend-bar"
                  onMouseEnter={() => setActive(i)}
                  onTouchStart={() => setActive(i)}
                  className={`flex-1 rounded-sm ${active === i ? "bg-bioaf-600" : "bg-bioaf-400"}`}
                  style={{ height: `${max > 0 ? Math.max((num(r.amount) / max) * 100, 2) : 0}%` }}
                />
              ))}
            </div>

            <div className="flex gap-0.5 mt-1" aria-hidden="true">
              {records.map((r, i) => (
                <div key={i} className="flex-1 text-center text-[10px] leading-none text-gray-500">
                  {(records.length - 1 - i) % step === 0 ? (
                    <span data-testid="cost-trend-label">{dayOf(r.date)}</span>
                  ) : null}
                </div>
              ))}
            </div>
          </div>

          {/* The tooltip is a hover, which a keyboard and a screen reader do not
              have. The same numbers, as text.

              The hiding class sits on a WRAPPER, never on the table itself. CSS
              treats height on a table box as a minimum rather than a definite
              size, so `sr-only`'s 1px height leaves a <table> as tall as its
              rows; `clip` then hides it from view while it still occupies the
              space, and since `sr-only` is absolutely positioned with no
              positioned ancestor, that space escapes the dashboard's scroll
              container and stretches the whole document. A <div> honours the
              1px box and clips the table inside it. */}
          <div className="sr-only">
            <table data-testid="cost-trend-table">
              <caption>Daily cost for the last 30 days</caption>
              <tbody>
                {records.map((r, i) => (
                  <tr key={i}>
                    <th scope="row">{shortDate(r.date)}</th>
                    <td>${num(r.amount).toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
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
