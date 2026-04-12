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
  plotThumbnailContentUrl: (plotId: number) => `http://localhost:8000/api/plots/${plotId}/thumbnail/content?token=fake`,
}));

jest.mock("@/components/layout/Sidebar", () => ({
  Sidebar: () => <div data-testid="sidebar">Sidebar</div>,
}));
jest.mock("@/components/layout/Header", () => ({
  Header: () => <div data-testid="header">Header</div>,
}));
jest.mock("@/components/shared/PlotModal", () => ({
  PlotModal: ({ title, metadata, onClose }: { title: string; metadata?: { experimentName?: string | null; projectName?: string | null; pipelineRunName?: string | null; sourceType?: string | null }; onClose: () => void }) => (
    <div data-testid="plot-modal">
      <span>{title}</span>
      {metadata?.experimentName && <span data-testid="modal-experiment">{metadata.experimentName}</span>}
      {metadata?.projectName && <span data-testid="modal-project">{metadata.projectName}</span>}
      {metadata?.pipelineRunName && <span data-testid="modal-pipeline">{metadata.pipelineRunName}</span>}
      {metadata?.sourceType && <span data-testid="modal-source">{metadata.sourceType}</span>}
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
      experiment_name: "RNA-seq Batch 1",
      project_name: "Cancer Genomics",
      pipeline_run_id: 5,
      pipeline_run_name: "nf-core/rnaseq",
      notebook_session_id: null,
      notebook_session_type: null,
      source_type: "pipeline",
      tags: ["fastqc", "qc"],
      thumbnail_url: null,
      indexed_at: "2026-03-19T00:00:00Z",
    },
    {
      id: 2,
      title: "adapter_content.png",
      file: { id: 11, filename: "adapter_content.png", gcs_uri: "gs://bucket/adapter.png", size_bytes: 2048, md5_checksum: null, file_type: "png", tags: [], uploader: null, upload_timestamp: "2026-03-19T00:00:00Z", created_at: "2026-03-19T00:00:00Z" },
      experiment_id: 2,
      experiment_name: "ATAC-seq Run",
      project_name: null,
      pipeline_run_id: null,
      pipeline_run_name: null,
      notebook_session_id: null,
      notebook_session_type: null,
      source_type: null,
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

test("plot modal receives metadata when thumbnail is clicked", async () => {
  render(<PlotArchivePage />);

  await waitFor(() => {
    const images = screen.getAllByRole("img");
    expect(images.length).toBeGreaterThan(0);
  });

  const images = screen.getAllByRole("img");
  fireEvent.click(images[0]);

  await waitFor(() => {
    expect(screen.getByTestId("plot-modal")).toBeInTheDocument();
  });

  expect(screen.getByTestId("modal-experiment")).toHaveTextContent("RNA-seq Batch 1");
  expect(screen.getByTestId("modal-project")).toHaveTextContent("Cancer Genomics");
  expect(screen.getByTestId("modal-pipeline")).toHaveTextContent("nf-core/rnaseq");
  expect(screen.getByTestId("modal-source")).toHaveTextContent("pipeline");
});

test("opens plot modal when failed-to-load thumbnail is clicked", async () => {
  render(<PlotArchivePage />);

  await waitFor(() => {
    expect(screen.getByText("fastqc_heatmap.png")).toBeInTheDocument();
  });

  await waitFor(() => {
    const images = screen.getAllByRole("img");
    expect(images.length).toBeGreaterThan(0);
  });

  // Simulate image load failure on the first thumbnail
  const images = screen.getAllByRole("img");
  fireEvent.error(images[0]);

  // The fallback should appear and be clickable
  const fallbacks = screen.getAllByText("No preview available");
  expect(fallbacks.length).toBeGreaterThan(0);

  fireEvent.click(fallbacks[0]);

  await waitFor(() => {
    expect(screen.getByTestId("plot-modal")).toBeInTheDocument();
  });
});
