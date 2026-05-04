import type { QCDashboardResponse } from "@/lib/types";
import { MetricSection } from "./MetricSection";
import { ChartSection } from "./ChartSection";

interface Props {
  dashboard: QCDashboardResponse;
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
        <p
          className="text-sm text-gray-600 mb-6"
          dangerouslySetInnerHTML={{
            __html: dashboard.summary_text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>"),
          }}
        />
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
