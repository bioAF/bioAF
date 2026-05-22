import { render, screen, fireEvent } from "@testing-library/react";
import { QCDashboardListItem } from "./QCDashboardListItem";
import type { QCDashboardSummary } from "@/lib/types";

function summary(overrides: Partial<QCDashboardSummary> = {}): QCDashboardSummary {
  return {
    id: 9,
    pipeline_run_id: 42,
    quality_rating: "excellent",
    cell_count: 5000,
    status: "ready",
    generated_at: "2026-05-14T00:00:00Z",
    project_name: "Project Alpha",
    experiment_name: "Alpha Exp 1",
    pipeline_name: "nf-core/scrnaseq",
    pipeline_version: "2.6.0",
    sample_external_ids: ["SAMPLE-001", "SAMPLE-002"],
    ...overrides,
  } as QCDashboardSummary;
}

it("renders run, pipeline, project, experiment, samples, cells and the rating badge", () => {
  render(<QCDashboardListItem dashboard={summary()} onClick={jest.fn()} />);
  expect(screen.getByText("Run #42")).toBeInTheDocument();
  expect(screen.getByText(/nf-core\/scrnaseq v2\.6\.0/)).toBeInTheDocument();
  expect(screen.getByText("Project Alpha")).toBeInTheDocument();
  expect(screen.getByText("Alpha Exp 1")).toBeInTheDocument();
  expect(screen.getByText(/SAMPLE-001, SAMPLE-002/)).toBeInTheDocument();
  expect(screen.getByText(/5,000 cells/)).toBeInTheDocument();
  expect(screen.getByText("excellent")).toBeInTheDocument();
});

it("truncates the sample list past three", () => {
  render(
    <QCDashboardListItem
      dashboard={summary({ sample_external_ids: ["A", "B", "C", "D", "E"] })}
      onClick={jest.fn()}
    />,
  );
  expect(screen.getByText(/A, B, C \+2 more/)).toBeInTheDocument();
});

it("fires onClick when the row is clicked", () => {
  const onClick = jest.fn();
  render(<QCDashboardListItem dashboard={summary()} onClick={onClick} />);
  fireEvent.click(screen.getByText("Run #42"));
  expect(onClick).toHaveBeenCalledTimes(1);
});
