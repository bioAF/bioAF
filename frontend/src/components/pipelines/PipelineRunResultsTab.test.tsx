import { render, screen, waitFor } from "@testing-library/react";

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn() },
  fileContentUrl: jest.fn(),
  plotThumbnailContentUrl: jest.fn(),
}));
jest.mock("@/hooks/useContentUrl", () => ({
  useFileContentUrl: () => "blob:fake",
  usePlotThumbnailContentUrl: () => "blob:fake",
}));
jest.mock("@/components/qc/GenericQCDashboard", () => ({
  GenericQCDashboard: () => <div data-testid="generic-qc" />,
}));
jest.mock("@/components/qc/QCAiReviewSection", () => ({
  QCAiReviewSection: (props: { pipelineRunId: number }) => (
    <div data-testid="qc-ai-section">ai:{props.pipelineRunId}</div>
  ),
}));
jest.mock("@/components/shared/PlotModal", () => ({ PlotModal: () => null }));
jest.mock("next/link", () => {
  return function MockLink({ href, children }: { href: string; children: React.ReactNode }) {
    return <a href={typeof href === "string" ? href : "#"}>{children}</a>;
  };
});

import { PipelineRunResultsTab } from "./PipelineRunResultsTab";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

beforeEach(() => mockGet.mockReset());

test("surfaces the AI Review section on the QC report for the run", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/qc-dashboards/by-run/42")) {
      return Promise.resolve({ pipeline_run_id: 42, metrics: {}, plots: [] });
    }
    if (url.startsWith("/api/plots")) return Promise.resolve({ plots: [] });
    return Promise.resolve({});
  });

  render(<PipelineRunResultsTab pipelineRunId={42} />);

  await waitFor(() => expect(screen.getByTestId("generic-qc")).toBeInTheDocument());
  expect(screen.getByTestId("qc-ai-section")).toHaveTextContent("ai:42");
});

test("shows the AI Review section even when no QC dashboard exists for the run", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/qc-dashboards/by-run/42")) return Promise.reject(new Error("404"));
    if (url.startsWith("/api/plots")) return Promise.resolve({ plots: [] });
    return Promise.resolve({});
  });

  render(<PipelineRunResultsTab pipelineRunId={42} />);

  await waitFor(() => expect(screen.getByText(/No QC dashboard yet/)).toBeInTheDocument());
  expect(screen.getByTestId("qc-ai-section")).toHaveTextContent("ai:42");
});
