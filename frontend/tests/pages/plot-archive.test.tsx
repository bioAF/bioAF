import { render, screen, waitFor, fireEvent } from "@testing-library/react";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
}));

const mockApiGet = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
  },
  fileContentUrl: (fileId: number) => `http://localhost:8000/api/files/${fileId}/content?token=fake`,
}));

jest.mock("@/components/layout/Sidebar", () => ({
  Sidebar: () => <div data-testid="sidebar">Sidebar</div>,
}));
jest.mock("@/components/layout/Header", () => ({
  Header: () => <div data-testid="header">Header</div>,
}));
jest.mock("@/components/shared/PlotModal", () => ({
  PlotModal: ({ title, onClose }: { title: string; onClose: () => void }) => (
    <div data-testid="plot-modal">
      <span>{title}</span>
      <button onClick={onClose}>Close</button>
    </div>
  ),
}));

import PlotArchivePage from "@/app/results/plot-archive/page";

const mockPlots = {
  plots: [
    {
      id: 1,
      title: "fastqc_heatmap.png",
      file: { id: 10, filename: "fastqc_heatmap.png", gcs_uri: "gs://bucket/heatmap.png", size_bytes: 1024, md5_checksum: null, file_type: "png", tags: [], uploader: null, upload_timestamp: "2026-03-19T00:00:00Z", created_at: "2026-03-19T00:00:00Z" },
      experiment_id: 1,
      pipeline_run_id: 5,
      notebook_session_id: null,
      tags: ["fastqc", "qc"],
      thumbnail_url: null,
      indexed_at: "2026-03-19T00:00:00Z",
    },
    {
      id: 2,
      title: "adapter_content.png",
      file: { id: 11, filename: "adapter_content.png", gcs_uri: "gs://bucket/adapter.png", size_bytes: 2048, md5_checksum: null, file_type: "png", tags: [], uploader: null, upload_timestamp: "2026-03-19T00:00:00Z", created_at: "2026-03-19T00:00:00Z" },
      experiment_id: 2,
      pipeline_run_id: null,
      notebook_session_id: null,
      tags: [],
      thumbnail_url: null,
      indexed_at: "2026-03-18T00:00:00Z",
    },
  ],
  total: 2,
  page: 1,
  page_size: 24,
};

const mockExperiments = {
  experiments: [
    { id: 1, name: "RNA-seq Batch 1" },
    { id: 2, name: "ATAC-seq Run" },
  ],
  total: 2,
  page: 1,
  page_size: 200,
};

const mockPipelineRuns = {
  runs: [
    { id: 5, pipeline_key: "nf-core/rnaseq" },
    { id: 6, pipeline_key: "cellranger" },
  ],
  total: 2,
  page: 1,
  page_size: 200,
};

beforeEach(() => {
  mockApiGet.mockReset();
  mockApiGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/plots")) return Promise.resolve(mockPlots);
    if (url.startsWith("/api/experiments")) return Promise.resolve(mockExperiments);
    if (url.startsWith("/api/pipeline-runs")) return Promise.resolve(mockPipelineRuns);
    return Promise.resolve({});
  });
});

test("renders plot archive with plots and filter controls", async () => {
  render(<PlotArchivePage />);

  await waitFor(() => {
    expect(screen.getByText("Plot Archive")).toBeInTheDocument();
  });

  await waitFor(() => {
    expect(screen.getByText("fastqc_heatmap.png")).toBeInTheDocument();
    expect(screen.getByText("adapter_content.png")).toBeInTheDocument();
  });

  expect(screen.getByText("2 plots")).toBeInTheDocument();
});

test("renders experiment and pipeline run filter dropdowns", async () => {
  render(<PlotArchivePage />);

  await waitFor(() => {
    expect(screen.getByText("All experiments")).toBeInTheDocument();
  });

  expect(screen.getByText("All runs")).toBeInTheDocument();
  expect(screen.getByText("RNA-seq Batch 1")).toBeInTheDocument();
  expect(screen.getByText("ATAC-seq Run")).toBeInTheDocument();
  expect(screen.getByText("nf-core/rnaseq #5")).toBeInTheDocument();
  expect(screen.getByText("cellranger #6")).toBeInTheDocument();
});

test("passes experiment_id filter to API", async () => {
  render(<PlotArchivePage />);

  await waitFor(() => {
    expect(screen.getByText("All experiments")).toBeInTheDocument();
  });

  const experimentSelect = screen.getByDisplayValue("All experiments");
  fireEvent.change(experimentSelect, { target: { value: "1" } });

  await waitFor(() => {
    const plotCalls = mockApiGet.mock.calls.filter(
      (c: string[]) => typeof c[0] === "string" && c[0].startsWith("/api/plots")
    );
    const lastCall = plotCalls[plotCalls.length - 1][0] as string;
    expect(lastCall).toContain("experiment_id=1");
  });
});

test("passes pipeline_run_id filter to API", async () => {
  render(<PlotArchivePage />);

  await waitFor(() => {
    expect(screen.getByText("All runs")).toBeInTheDocument();
  });

  const runSelect = screen.getByDisplayValue("All runs");
  fireEvent.change(runSelect, { target: { value: "5" } });

  await waitFor(() => {
    const plotCalls = mockApiGet.mock.calls.filter(
      (c: string[]) => typeof c[0] === "string" && c[0].startsWith("/api/plots")
    );
    const lastCall = plotCalls[plotCalls.length - 1][0] as string;
    expect(lastCall).toContain("pipeline_run_id=5");
  });
});

test("plot thumbnails use content URL instead of download endpoint", async () => {
  render(<PlotArchivePage />);

  await waitFor(() => {
    const images = screen.getAllByRole("img");
    expect(images.length).toBeGreaterThan(0);
  });

  const images = screen.getAllByRole("img");
  expect(images[0]).toHaveAttribute("src", "http://localhost:8000/api/files/10/content?token=fake");
  expect(images[1]).toHaveAttribute("src", "http://localhost:8000/api/files/11/content?token=fake");

  // Must NOT call the download endpoint for inline display
  const downloadCalls = mockApiGet.mock.calls.filter(
    (c: string[]) => typeof c[0] === "string" && c[0].includes("/download")
  );
  expect(downloadCalls).toHaveLength(0);
});

test("renders tags on plot cards", async () => {
  render(<PlotArchivePage />);

  await waitFor(() => {
    expect(screen.getByText("fastqc")).toBeInTheDocument();
    expect(screen.getByText("qc")).toBeInTheDocument();
  });
});

test("shows no plots message when empty", async () => {
  mockApiGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/plots")) return Promise.resolve({ plots: [], total: 0, page: 1, page_size: 24 });
    if (url.startsWith("/api/experiments")) return Promise.resolve(mockExperiments);
    if (url.startsWith("/api/pipeline-runs")) return Promise.resolve(mockPipelineRuns);
    return Promise.resolve({});
  });

  render(<PlotArchivePage />);

  await waitFor(() => {
    expect(screen.getByText("No plots found.")).toBeInTheDocument();
  });
});

test("opens plot modal when thumbnail is clicked", async () => {
  render(<PlotArchivePage />);

  await waitFor(() => {
    expect(screen.getByText("fastqc_heatmap.png")).toBeInTheDocument();
  });

  // Wait for signed URLs to load
  await waitFor(() => {
    const images = screen.getAllByRole("img");
    expect(images.length).toBeGreaterThan(0);
  });

  const images = screen.getAllByRole("img");
  fireEvent.click(images[0]);

  await waitFor(() => {
    expect(screen.getByTestId("plot-modal")).toBeInTheDocument();
  });
});
