import { MetricCard, HeroMetric } from "./MetricCard";
import type { QCMetricSpec, QCSection } from "@/lib/types";

interface Props {
  section: QCSection;
  metricSpecs: Record<string, QCMetricSpec>;
  values: Record<string, unknown>;
}

export function MetricSection({ section, metricSpecs, values }: Props) {
  const renderable = section.metrics
    .map((key) => ({ key, spec: metricSpecs[key], value: values[key] }))
    .filter((m) => m.spec && m.value != null);

  if (renderable.length === 0) return null;

  if (section.layout === "hero") {
    return (
      <div className="bg-gray-50 rounded-lg p-6 mb-6">
        <div className="flex flex-wrap justify-around gap-6">
          {renderable.map(({ key, spec, value }) => (
            <HeroMetric key={key} value={value} spec={spec} />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="mt-6 mb-3">
      {section.title && (
        <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide mb-3">{section.title}</h3>
      )}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {renderable.map(({ key, spec, value }) => (
          <MetricCard key={key} value={value} spec={spec} />
        ))}
      </div>
    </div>
  );
}
