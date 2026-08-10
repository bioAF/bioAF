import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { QCReportModal } from "./QCReportModal";
import { api } from "@/lib/api";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));
jest.mock("@/hooks/useContentUrl", () => ({ useFileContentUrl: () => "blob:fake" }));
jest.mock("@/components/shared/PlotModal", () => ({ PlotModal: () => null }));
jest.mock("./GenericQCDashboard", () => ({
  GenericQCDashboard: () => <div data-testid="generic-qc" />,
}));
jest.mock("./QCAiReviewSection", () => ({
  QCAiReviewSection: (props: { pipelineRunId: number }) => (
    <div data-testid="qc-ai-section">ai:{props.pipelineRunId}</div>
  ),
}));
jest.mock("next/link", () => {
  return function MockLink({ href, children }: { href: string; children: React.ReactNode }) {
    return <a href={typeof href === "string" ? href : "#"}>{children}</a>;
  };
});

const mockGet = api.get as jest.Mock;

function dashboard() {
  return {
    id: 9,
    pipeline_run_id: 42,
    project_name: "Project Alpha",
    experiment_name: "Exp One",
    pipeline_name: "nf-core/scrnaseq",
    pipeline_version: "2.6.0",
    metrics: { quality_rating: "good" },
    plots: [],
  };
}

beforeEach(() => mockGet.mockReset());

it("loads the real QC report by dashboard id and surfaces the AI Review section", async () => {
  mockGet.mockResolvedValue(dashboard());
  render(<QCReportModal dashboardId={9} onClose={jest.fn()} />);

  expect(await screen.findByTestId("generic-qc")).toBeInTheDocument();
  expect(mockGet).toHaveBeenCalledWith("/api/qc-dashboards/9");
  expect(screen.getByTestId("qc-ai-section")).toHaveTextContent("ai:42");
  expect(screen.getByRole("link", { name: /Run #42/i })).toHaveAttribute(
    "href",
    "/pipelines/runs/42",
  );
});

it("closes on backdrop click but not on inside click", async () => {
  mockGet.mockResolvedValue(dashboard());
  const onClose = jest.fn();
  render(<QCReportModal dashboardId={9} onClose={onClose} />);
  await screen.findByTestId("generic-qc");

  fireEvent.click(screen.getByTestId("generic-qc")); // inside
  expect(onClose).not.toHaveBeenCalled();

  // The backdrop belongs to the shared Modal shell now, so it carries the
  // shell's testid. Same click, same expectation.
  fireEvent.click(screen.getByTestId("modal-backdrop"));
  expect(onClose).toHaveBeenCalledTimes(1);
});

it("closes on Escape", async () => {
  mockGet.mockResolvedValue(dashboard());
  const onClose = jest.fn();
  render(<QCReportModal dashboardId={9} onClose={onClose} />);
  await screen.findByTestId("generic-qc");

  act(() => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  });
  expect(onClose).toHaveBeenCalledTimes(1);
});

it("shows an error when the dashboard fails to load", async () => {
  mockGet.mockRejectedValue(new Error("not found"));
  render(<QCReportModal dashboardId={9} onClose={jest.fn()} />);
  await waitFor(() => expect(screen.getByText("not found")).toBeInTheDocument());
});
