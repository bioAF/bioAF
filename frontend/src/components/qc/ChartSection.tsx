import {
  BarcodeRankChart,
  StarAlignmentChart,
  BaseQualityChart,
  GCContentChart,
  DuplicationChart,
} from "@/components/shared/QCCharts";
import type { QCChartSpec } from "@/lib/types";
import { getNested } from "./format";

interface Props {
  charts: QCChartSpec[];
  values: Record<string, unknown>;
}

/** Maps qc_config.charts[].type to a renderer. The data shape each renderer
 *  expects is the same as before the generic refactor -- the chart components
 *  themselves are unchanged. */
export function ChartSection({ charts, values }: Props) {
  type Item = { chart: QCChartSpec; data: unknown };
  const items: Item[] = charts.flatMap((chart): Item[] => {
    const key = chart.metric_key ?? chart.type;
    const data = getNested(values, key);
    if (data == null) return [];
    return [{ chart, data }];
  });

  if (items.length === 0) return null;

  return (
    <>
      <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide mt-6 mb-3">
        Interactive Charts
      </h3>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {items.map(({ chart, data }, i) => {
          switch (chart.type) {
            case "barcode_rank":
              return (
                <div key={i} className="lg:col-span-2">
                  <BarcodeRankChart data={data as [number, number][]} />
                </div>
              );
            case "star_alignment":
              return <StarAlignmentChart key={i} data={data as { name: string; value: number }[]} />;
            case "base_quality":
              return <BaseQualityChart key={i} data={data as [number, number][]} />;
            case "gc_content":
              return (
                <GCContentChart
                  key={i}
                  data={data as { sample: [number, number][]; theoretical?: [number, number][] }}
                />
              );
            case "duplication":
              return <DuplicationChart key={i} data={data as [number, number][]} />;
            default:
              return null;
          }
        })}
      </div>
    </>
  );
}
