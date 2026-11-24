import { render, screen, waitFor } from "@testing-library/react";
import {
  BarcodeRankChart,
  BaseQualityChart,
  DuplicationChart,
  GCContentChart,
  StarAlignmentChart,
} from "./QCCharts";
import SnapshotComparison from "../SnapshotComparison";

/**
 * Draws the QC and snapshot-comparison charts with the real Recharts, not a
 * mock, so a Recharts, Redux Toolkit, Immer or es-toolkit update that breaks
 * how these charts lay out their data fails here rather than on a dashboard.
 *
 * jsdom does no layout, so ResponsiveContainer is given a fixed size through
 * ResizeObserver and getBoundingClientRect, and SVG paths get a length for
 * the line animation. jsdom cannot measure text, so axis tick labels are not
 * asserted; bars, points, areas, lines and legends are.
 */

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));
import { api } from "@/lib/api";

const WIDTH = 800;
const HEIGHT = 400;

beforeAll(() => {
  class FixedSizeObserver {
    constructor(private readonly callback: ResizeObserverCallback) {}
    observe(target: Element) {
      this.callback(
        [{ target, contentRect: { width: WIDTH, height: HEIGHT } } as unknown as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
    }
    unobserve() {}
    disconnect() {}
  }
  global.ResizeObserver = FixedSizeObserver as unknown as typeof ResizeObserver;
  // Only the chart container has a size; everything else (legends, tooltips)
  // measures as empty, as jsdom reports it.
  const sized = (el: Element) => el.classList.contains("recharts-responsive-container");
  const rect = (w: number, h: number) =>
    ({ width: w, height: h, top: 0, left: 0, right: w, bottom: h, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
  jest
    .spyOn(HTMLElement.prototype, "getBoundingClientRect")
    .mockImplementation(function (this: HTMLElement) {
      return sized(this) ? rect(WIDTH, HEIGHT) : rect(0, 0);
    });
  jest.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) {
    return sized(this) ? WIDTH : 0;
  });
  jest.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(function (this: HTMLElement) {
    return sized(this) ? HEIGHT : 0;
  });
  Object.defineProperty(SVGElement.prototype, "getTotalLength", {
    configurable: true,
    value: () => 100,
  });
});

afterAll(() => jest.restoreAllMocks());

async function chartSurface(container: HTMLElement) {
  await waitFor(() => expect(container.querySelector("svg.recharts-surface")).not.toBeNull());
  return container;
}

test("the STAR alignment chart draws one bar per category", async () => {
  const { container } = render(
    <StarAlignmentChart
      data={[
        { name: "Uniquely mapped", value: 85.2 },
        { name: "Multi-mapped", value: 9.1 },
        { name: "Unmapped", value: 5.7 },
      ]}
    />,
  );
  await chartSurface(container);
  await waitFor(() =>
    expect(container.querySelectorAll(".recharts-bar-rectangle path")).toHaveLength(3),
  );
});

test("the duplication chart draws one bar per level", async () => {
  const { container } = render(
    <DuplicationChart
      data={[
        [1, 70],
        [2, 20],
        [3, 10],
      ]}
    />,
  );
  await chartSurface(container);
  await waitFor(() =>
    expect(container.querySelectorAll(".recharts-bar-rectangle path")).toHaveLength(3),
  );
});

test("the GC content chart draws the sample and theoretical lines with a legend", async () => {
  const { container } = render(
    <GCContentChart
      data={{
        sample: [
          [40, 10],
          [50, 30],
          [60, 12],
        ],
        theoretical: [
          [40, 12],
          [50, 28],
          [60, 11],
        ],
      }}
    />,
  );
  await chartSurface(container);
  await waitFor(() =>
    expect(container.querySelectorAll("path.recharts-line-curve")).toHaveLength(2),
  );
  expect(screen.getByText("Sample")).toBeInTheDocument();
  expect(screen.getByText("Theoretical")).toBeInTheDocument();
});

test("the base quality chart draws its area", async () => {
  const { container } = render(
    <BaseQualityChart
      data={[
        [1, 32],
        [50, 36],
        [100, 30],
      ]}
    />,
  );
  await chartSurface(container);
  await waitFor(() =>
    expect(container.querySelector("path.recharts-area-area")?.getAttribute("d")).toBeTruthy(),
  );
});

test("the barcode rank chart plots every barcode on log axes", async () => {
  const { container } = render(
    <BarcodeRankChart
      data={[
        [1, 50000],
        [10, 20000],
        [100, 3000],
        [1000, 40],
      ]}
    />,
  );
  await chartSurface(container);
  await waitFor(() =>
    expect(container.querySelectorAll(".recharts-scatter-symbol")).toHaveLength(4),
  );
});

test("a snapshot comparison draws cluster bars per snapshot and the cell count line", async () => {
  const snapshot = (id: number, label: string) => ({
    id,
    label,
    user_name: "Ada",
    created_at: "2026-11-01T00:00:00Z",
    cell_count: 1000,
    gene_count: 2000,
    figure_url: null,
  });
  (api.get as jest.Mock).mockResolvedValue({
    snapshots: [snapshot(1, "Before"), snapshot(2, "After")],
    parameter_diff: [],
    embedding_diff: [],
    clustering_diff: [
      {
        clustering_name: "leiden",
        n_clusters: { 1: 2, 2: 2 },
        distributions: { 1: { "0": 600, "1": 400 }, 2: { "0": 550, "1": 450 } },
        present_in: [1, 2],
      },
    ],
    command_log_diff: null,
    cell_count_series: [
      { snapshot_id: 1, label: "Before", cell_count: 1000, created_at: "2026-11-01T00:00:00Z" },
      { snapshot_id: 2, label: "After", cell_count: 950, created_at: "2026-11-02T00:00:00Z" },
    ],
  });

  const { container } = render(<SnapshotComparison url="/api/compare" />);
  await screen.findByText(/cluster distribution: leiden/i);
  await waitFor(() =>
    expect(container.querySelectorAll(".recharts-bar-rectangle path")).toHaveLength(4),
  );
  await waitFor(() =>
    expect(container.querySelectorAll("path.recharts-line-curve")).toHaveLength(1),
  );
});
