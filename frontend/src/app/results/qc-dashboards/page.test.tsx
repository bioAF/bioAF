import { render, screen, waitFor } from "@testing-library/react";

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
  },
}));

jest.mock("@/components/layout/Sidebar", () => ({ Sidebar: () => null }));
jest.mock("@/components/layout/Header", () => ({ Header: () => null }));
jest.mock("@/components/shared/ContentLoading", () => ({ ContentLoading: () => null }));
jest.mock("@/components/shared/PlotModal", () => ({ PlotModal: () => null }));
jest.mock("@/components/shared/ExportPdfButton", () => ({ ExportPdfButton: () => null }));
jest.mock("@/components/qc/GenericQCDashboard", () => ({ GenericQCDashboard: () => null }));
jest.mock("@/hooks/useContentUrl", () => ({
  useFileContentUrl: () => "blob:fake",
  usePlotThumbnailContentUrl: () => "blob:fake",
}));

import QCDashboardsPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
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
