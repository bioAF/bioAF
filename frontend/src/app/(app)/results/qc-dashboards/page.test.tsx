import { render, screen, waitFor } from "@testing-library/react";

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
  },
}));

let mockRunParam: string | null = null;
jest.mock("next/navigation", () => ({
  useSearchParams: () => ({ get: (key: string) => (key === "run" ? mockRunParam : null) }),
}));

jest.mock("next/link", () => {
  return function MockLink({ href, children }: { href: string; children: React.ReactNode }) {
    return <a href={typeof href === "string" ? href : "#"}>{children}</a>;
  };
});

jest.mock("@/components/shared/ContentLoading", () => ({ ContentLoading: () => null }));
jest.mock("@/components/shared/PlotModal", () => ({ PlotModal: () => null }));
jest.mock("@/components/shared/ExportPdfButton", () => ({ ExportPdfButton: () => null }));
jest.mock("@/components/qc/GenericQCDashboard", () => ({ GenericQCDashboard: () => null }));
jest.mock("@/components/qc/QCAiReviewSection", () => ({
  QCAiReviewSection: (props: { pipelineRunId: number }) => (
    <div data-testid="qc-ai-section">ai:{props.pipelineRunId}</div>
  ),
}));
jest.mock("@/hooks/useContentUrl", () => ({
  useFileContentUrl: () => "blob:fake",
  usePlotThumbnailContentUrl: () => "blob:fake",
}));

import QCDashboardsPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  mockRunParam = null;
});

describe("QCDashboardsPage list view", () => {
  test("each row shows project, experiment, sample IDs, and pipeline name", async () => {
    mockGet.mockResolvedValue([
      {
        id: 1,
        pipeline_run_id: 42,
        quality_rating: "good",
        cell_count: 5000,
        status: "ready",
        generated_at: "2026-05-14T00:00:00Z",
        project_name: "Project Alpha",
        experiment_name: "Alpha Exp 1",
        pipeline_name: "nf-core/scrnaseq",
        pipeline_version: "2.6.0",
        sample_external_ids: ["SAMPLE-001", "SAMPLE-002"],
      },
    ]);

    render(<QCDashboardsPage />);

    await waitFor(() => expect(screen.queryByText("Run #42")).toBeTruthy());
    expect(screen.getByText("Project Alpha")).toBeTruthy();
    expect(screen.getByText("Alpha Exp 1")).toBeTruthy();
    expect(screen.getByText(/nf-core\/scrnaseq/)).toBeTruthy();
    expect(screen.getByText(/v2\.6\.0/)).toBeTruthy();
    expect(screen.getByText(/SAMPLE-001/)).toBeTruthy();
    expect(screen.getByText(/SAMPLE-002/)).toBeTruthy();
  });

  test("renders gracefully when context fields are missing", async () => {
    mockGet.mockResolvedValue([
      {
        id: 2,
        pipeline_run_id: 7,
        quality_rating: "pending_review",
        cell_count: null,
        status: "generating",
        generated_at: null,
        project_name: null,
        experiment_name: null,
        pipeline_name: null,
        pipeline_version: null,
        sample_external_ids: [],
      },
    ]);

    render(<QCDashboardsPage />);
    await waitFor(() => expect(screen.queryByText("Run #7")).toBeTruthy());
    // Should not crash; missing context should not produce literal "null" strings.
    expect(screen.queryByText("null")).toBeNull();
  });
});

describe("QCDashboardsPage deep link", () => {
  test("?run= opens the dashboard for that run directly", async () => {
    mockRunParam = "42";
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/by-run/42")) {
        return Promise.resolve({
          pipeline_run_id: 42,
          metrics: { quality_rating: "good" },
          plots: [],
        });
      }
      return Promise.resolve([]); // list
    });

    render(<QCDashboardsPage />);

    // Detail view opened: it shows the "Back to list" control and the run link.
    await waitFor(() => expect(screen.queryByText("Back to list")).toBeTruthy());
    expect(screen.getByRole("link", { name: /Run #42/i })).toHaveAttribute(
      "href",
      "/pipelines/runs/42",
    );
    expect(mockGet).toHaveBeenCalledWith("/api/qc-dashboards/by-run/42");
    // The AI Review section is surfaced on the report for that run.
    expect(screen.getByTestId("qc-ai-section")).toHaveTextContent("ai:42");
  });

  test("without ?run= it shows the list, not a detail", async () => {
    mockGet.mockResolvedValue([]);
    render(<QCDashboardsPage />);
    await waitFor(() => expect(screen.queryByText("QC Dashboards")).toBeTruthy());
    expect(screen.queryByText(/Run #\d+/)).toBeNull();
  });

  test("the detail names the report by context and links back to the run", async () => {
    mockRunParam = "42";
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/by-run/42")) {
        return Promise.resolve({
          pipeline_run_id: 42,
          project_name: "Project Aardvark",
          experiment_name: "Experiment Beluga",
          pipeline_name: "nf-core/scrnaseq",
          pipeline_version: "2.6.0",
          metrics: { quality_rating: "good" },
          plots: [],
        });
      }
      return Promise.resolve([]);
    });

    render(<QCDashboardsPage />);

    const runLink = await screen.findByRole("link", { name: /Run #42/i });
    expect(runLink).toHaveAttribute("href", "/pipelines/runs/42");
    expect(screen.getByText(/Project Aardvark \/ Experiment Beluga \/ nf-core\/scrnaseq v2\.6\.0/)).toBeInTheDocument();
  });
});
