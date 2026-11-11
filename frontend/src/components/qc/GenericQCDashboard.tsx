import type { ReactNode } from "react";
import type { QCDashboardResponse } from "@/lib/types";
import { MetricSection } from "./MetricSection";
import { ChartSection } from "./ChartSection";

interface Props {
  dashboard: QCDashboardResponse;
}

/** The summary's only formatting: each complete `**...**` span on one line
 *  becomes strong text. Everything else, including Markdown- or HTML-shaped
 *  text from a custom pipeline or an error message, stays literal text. */
function boldSpans(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /\*\*(.+?)\*\*/g;
  let last = 0;
  for (const match of Array.from(text.matchAll(pattern))) {
    const start = match.index ?? 0;
    if (start > last) nodes.push(text.slice(last, start));
    nodes.push(<strong key={start}>{match[1]}</strong>);
    last = start + match[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** Generic, config-driven body for the QC dashboard detail view.
 *  Sections, metric cards, charts, and metric formatting all come from
 *  dashboard.qc_config -- so a new pipeline template just needs to ship
 *  its render config (no page changes). */
export function GenericQCDashboard({ dashboard }: Props) {
  const cfg = dashboard.qc_config;
  const values = dashboard.raw_metrics ?? {};

  return (
    <>
      {dashboard.summary_text && (
        <p className="text-sm text-gray-600 mb-6">{boldSpans(dashboard.summary_text)}</p>
      )}

      {cfg.sections.map((section) => (
        <MetricSection
          key={section.id}
          section={section}
          metricSpecs={cfg.metrics}
          values={values}
        />
      ))}

      <ChartSection charts={cfg.charts} values={values} />
    </>
  );
}
