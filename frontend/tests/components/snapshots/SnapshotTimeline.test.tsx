import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import SnapshotTimeline from "@/components/SnapshotTimeline";
import type { AnalysisSnapshot } from "@/lib/types";

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
  },
}));

// SnapshotComparison is lazily imported: mock it so the dynamic import resolves
jest.mock("@/components/SnapshotComparison", () => ({
  __esModule: true,
  default: () => <div data-testid="snapshot-comparison">Comparison content</div>,
}));

const makeSnapshot = (overrides: Partial<AnalysisSnapshot> = {}): AnalysisSnapshot => ({
  id: 1,
  experiment_id: 10,
  project_id: null,
  notebook_session_id: 5,
  user_id: 1,
  user_name: "Dr. Sarah",
  label: "After QC filtering",
  notes: null,
  object_type: "anndata",
  cell_count: 5000,
  gene_count: 20000,
  cluster_count: 12,
  starred: false,
  figure_url: null,
  created_at: "2026-03-10T12:00:00Z",
  ...overrides,
});

describe("SnapshotTimeline", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiPost.mockReset();
    mockApiGet.mockResolvedValue({ snapshots: [], total: 0 });
  });

  it("shows loading state initially", () => {
    mockApiGet.mockImplementation(() => new Promise(() => {})); // never resolves
    render(<SnapshotTimeline experimentId={10} />);
    expect(screen.getByText("Loading snapshots...")).toBeInTheDocument();
  });

  it("shows empty state when no snapshots", async () => {
    render(<SnapshotTimeline experimentId={10} />);
    await waitFor(() => {
      expect(screen.getByText("No Analysis Snapshots")).toBeInTheDocument();
      expect(screen.getByText(/bioaf\.snapshot/)).toBeInTheDocument();
    });
  });

  it("fetches snapshots with experiment_id param", async () => {
    render(<SnapshotTimeline experimentId={10} />);
    await waitFor(() => {
      expect(mockApiGet).toHaveBeenCalledWith(
        expect.stringContaining("experiment_id=10")
      );
    });
  });

  it("fetches snapshots with project_id param", async () => {
    render(<SnapshotTimeline projectId={42} />);
    await waitFor(() => {
      expect(mockApiGet).toHaveBeenCalledWith(
        expect.stringContaining("project_id=42")
      );
    });
  });

  it("renders snapshots grouped by notebook session", async () => {
    const snaps = [
      makeSnapshot({ id: 1, notebook_session_id: 5, label: "QC done" }),
      makeSnapshot({ id: 2, notebook_session_id: 5, label: "Clustered" }),
      makeSnapshot({ id: 3, notebook_session_id: 6, label: "DEG analysis", user_name: "Alex" }),
    ];
    mockApiGet.mockResolvedValue({ snapshots: snaps, total: 3 });

    render(<SnapshotTimeline experimentId={10} />);

    await waitFor(() => {
      expect(screen.getByText("Session 5")).toBeInTheDocument();
      expect(screen.getByText("Session 6")).toBeInTheDocument();
      expect(screen.getByText("QC done")).toBeInTheDocument();
      expect(screen.getByText("Clustered")).toBeInTheDocument();
      expect(screen.getByText("DEG analysis")).toBeInTheDocument();
    });
  });

  it("renders snapshot with AnnData type badge", async () => {
    mockApiGet.mockResolvedValue({
      snapshots: [makeSnapshot({ object_type: "anndata" })],
      total: 1,
    });
    render(<SnapshotTimeline experimentId={10} />);
    await waitFor(() => {
      expect(screen.getByText("AnnData")).toBeInTheDocument();
    });
  });

  it("renders snapshot with Seurat type badge", async () => {
    mockApiGet.mockResolvedValue({
      snapshots: [makeSnapshot({ object_type: "seurat" })],
      total: 1,
    });
    render(<SnapshotTimeline experimentId={10} />);
    await waitFor(() => {
      expect(screen.getByText("Seurat")).toBeInTheDocument();
    });
  });

  it("star button calls toggle API and updates UI", async () => {
    mockApiGet.mockResolvedValue({
      snapshots: [makeSnapshot({ id: 1, starred: false })],
      total: 1,
    });
    mockApiPost.mockResolvedValue({ ...makeSnapshot({ id: 1, starred: true }) });

    render(<SnapshotTimeline experimentId={10} />);
    await waitFor(() => screen.getByTitle("Star"));

    fireEvent.click(screen.getByTitle("Star"));
    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith("/api/snapshots/1/star");
    });
  });

  it("Compare Selected button appears when 2+ snapshots are selected", async () => {
    const snaps = [
      makeSnapshot({ id: 1, label: "Snap 1" }),
      makeSnapshot({ id: 2, label: "Snap 2" }),
    ];
    mockApiGet.mockResolvedValue({ snapshots: snaps, total: 2 });

    render(<SnapshotTimeline experimentId={10} />);
    await waitFor(() => screen.getByText("Snap 1"));

    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[0]);
    fireEvent.click(checkboxes[1]);

    expect(screen.getByText(/Compare Selected/)).toBeInTheDocument();
  });

  it("does not show Compare Selected with only one snapshot selected", async () => {
    mockApiGet.mockResolvedValue({
      snapshots: [makeSnapshot({ id: 1, label: "Only one" })],
      total: 1,
    });
    render(<SnapshotTimeline experimentId={10} />);
    await waitFor(() => screen.getByText("Only one"));

    const [checkbox] = screen.getAllByRole("checkbox");
    fireEvent.click(checkbox);

    expect(screen.queryByText(/Compare Selected/)).not.toBeInTheDocument();
  });

  it("opens comparison modal when Compare Selected is clicked", async () => {
    const snaps = [
      makeSnapshot({ id: 1, label: "Snap 1" }),
      makeSnapshot({ id: 2, label: "Snap 2" }),
    ];
    mockApiGet.mockResolvedValue({ snapshots: snaps, total: 2 });

    render(<SnapshotTimeline experimentId={10} />);
    await waitFor(() => screen.getByText("Snap 1"));

    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[0]);
    fireEvent.click(checkboxes[1]);
    fireEvent.click(screen.getByText(/Compare Selected/));

    await waitFor(() => {
      expect(screen.getByText(/Comparing 2 Snapshots/)).toBeInTheDocument();
    });
  });
});
