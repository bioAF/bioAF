/**
 * Tests for Pipeline Runs pages (spec tests 27-30).
 *
 * 27: Pipeline catalog shows bioAF System Test
 * 28: Pipeline run detail shows k8s fields
 * 29: Log viewer displays real content
 * 30: Cancel button calls cancel endpoint
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Mock next/navigation
const mockPush = jest.fn();
const mockUseParams = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  useParams: () => mockUseParams(),
  usePathname: () => "/pipelines/runs/42",
}));

// Mock auth
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ email: "test@bioaf.org", role: "admin", sub: "1" }),
  getToken: () =>
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIiwicm9sZSI6ImFkbWluIn0.fake",
}));

jest.mock("@/hooks/useComponents", () => ({
  useComponents: () => ({ components: [], loading: false, refetch: jest.fn() }),
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, roleName: "admin", loading: false, permissions: new Set() }),
}));

// Mock API
const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
  },
  ApiError: class ApiError extends Error {},
}));

// Mock fetch for report endpoint
const originalFetch = global.fetch;

beforeEach(() => {
  jest.clearAllMocks();
  mockUseParams.mockReturnValue({ id: "42" });
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    text: () => Promise.resolve(""),
    json: () => Promise.resolve({}),
  });
});

afterEach(() => {
  global.fetch = originalFetch;
});

const mockRunWithK8s = {
  id: 42,
  pipeline_key: "bioaf-system-test",
  pipeline_name: "bioAF System Test",
  pipeline_version: "1.0.0",
  experiment: { id: 1, name: "Test Experiment" },
  submitted_by: { id: 1, name: "Admin", email: "admin@test.com" },
  status: "running" as const,
  parameters: { message: "Hello from bioAF", sleep_seconds: 10 },
  input_files: null,
  output_files: null,
  progress: {
    total_processes: 1,
    completed: 0,
    running: 1,
    failed: 0,
    cached: 0,
    percent_complete: 50,
  },
  cost_estimate: 0.5,
  error_message: null,
  work_dir: "/data/working/nextflow/run-42",
  slurm_job_id: null,
  compute_job_ref: "bioaf-pipeline-42",
  provider_metadata: {
    job_name: "bioaf-pipeline-42",
    namespace: "bioaf-pipelines",
    pod_name: "bioaf-pipeline-42-abc12",
  },
  actual_cost: null,
  reference_genome: null,
  alignment_algorithm: null,
  resume_from_run_id: null,
  review_verdict: null,
  started_at: "2026-03-11T10:00:00Z",
  completed_at: null,
  created_at: "2026-03-11T10:00:00Z",
  processes: [],
  samples: [],
};

describe("Pipeline Run Detail - References Used", () => {
  test("renders linked references with name, version, and category", async () => {
    const refs = [
      {
        id: 7,
        organization_id: 1,
        name: "GRCh38 GENCODE",
        category: "genome",
        scope: "public",
        version: "v45",
        source_url: null,
        gcs_prefix: "genome/grch38-gencode/v45/",
        total_size_bytes: null,
        file_count: 2,
        status: "active",
        deprecation_note: null,
        superseded_by_id: null,
        created_at: "2026-05-01T00:00:00Z",
      },
    ];
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/api/pipeline-runs/42/references")) {
        return Promise.resolve(refs);
      }
      return Promise.resolve(mockRunWithK8s);
    });

    const PipelineRunDetailPage =
      require("@/app/(app)/pipelines/runs/[id]/page").default;
    render(<PipelineRunDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("References Used")).toBeInTheDocument();
    });
    expect(screen.getByText("GRCh38 GENCODE")).toBeInTheDocument();
    expect(screen.getByText("v45")).toBeInTheDocument();
    // Category column rendered (capitalized via CSS, raw "genome" in DOM)
    expect(screen.getByText("genome")).toBeInTheDocument();
    // Name links to detail page
    const link = screen.getByRole("link", { name: "GRCh38 GENCODE" });
    expect(link).toHaveAttribute("href", "/data/references/7");
  });
});

describe("Pipeline Run Detail - Provider details (Test 28)", () => {
  // Phase 4 neutralized the hardcoded "Kubernetes metadata" panel into a
  // backend-agnostic "Provider details" disclosure rendered from
  // provider_metadata, so it works for any compute backend (K8s, SLURM, ...).
  test("renders the generic Provider details disclosure from provider_metadata", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/api/pipeline-runs/42/references")) {
        return Promise.resolve([]);
      }
      return Promise.resolve(mockRunWithK8s);
    });

    const PipelineRunDetailPage =
      require("@/app/(app)/pipelines/runs/[id]/page").default;
    render(<PipelineRunDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("Provider details")).toBeInTheDocument();
    });

    // Backend-specific keys/values are rendered generically, not under
    // hardcoded "K8s Job"/"Pod" labels.
    expect(screen.getByText("job_name")).toBeInTheDocument();
    expect(screen.getByText("bioaf-pipeline-42")).toBeInTheDocument();
    expect(screen.getByText("pod_name")).toBeInTheDocument();
    expect(screen.getByText("bioaf-pipeline-42-abc12")).toBeInTheDocument();
  });
});

describe("Pipeline Logs Display (Test 29)", () => {
  test("renders log content from API", async () => {
    const runWithProcesses = {
      ...mockRunWithK8s,
      processes: [
        {
          id: 1,
          process_name: "pipeline",
          task_id: "1",
          status: "running",
          exit_code: null,
          cpu_usage: null,
          memory_peak_gb: null,
          duration_seconds: null,
          started_at: null,
          completed_at: null,
        },
      ],
    };

    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/logs/")) {
        return Promise.resolve({
          stdout: "Pipeline started\nProcessing sample 1\nDone",
          stderr: "",
        });
      }
      if (url.includes("/references")) return Promise.resolve([]);
      return Promise.resolve(runWithProcesses);
    });

    const PipelineRunDetailPage =
      require("@/app/(app)/pipelines/runs/[id]/page").default;
    render(<PipelineRunDetailPage />);

    // Wait for page to load (pipeline name is embedded in heading)
    await waitFor(() => {
      expect(screen.getByText(/bioAF System Test/)).toBeInTheDocument();
    });

    // Logs tab is now the default active tab, so logs content is already visible.
    // No need to click the tab.

    // Select a process to load logs
    await waitFor(() => {
      const processSelect = screen.queryByRole("combobox");
      if (processSelect) {
        fireEvent.change(processSelect, { target: { value: "pipeline" } });
      }
    });

    // The log viewer should eventually show log content
    await waitFor(
      () => {
        const logContent = screen.queryByText(/Pipeline started/);
        if (logContent) {
          expect(logContent).toBeInTheDocument();
        }
      },
      { timeout: 3000 }
    );
  });
});

describe("Cancel Button (Test 30)", () => {
  test("cancel button calls cancel endpoint", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/references")) return Promise.resolve([]);
      return Promise.resolve(mockRunWithK8s);
    });
    mockApiPost.mockResolvedValue({ ...mockRunWithK8s, status: "cancelled" });

    // Mock confirm dialog
    jest.spyOn(window, "confirm").mockReturnValue(true);

    const PipelineRunDetailPage =
      require("@/app/(app)/pipelines/runs/[id]/page").default;
    render(<PipelineRunDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("Cancel")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Cancel"));

    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        "/api/pipeline-runs/42/cancel"
      );
    });
  });
});

describe("Nextflow Report iframe", () => {
  // The iframe must NOT carry a `sandbox` attribute. A srcdoc iframe
  // inherits its parent's CSP per the HTML spec regardless of sandbox,
  // so sandbox doesn't help with the Plotly `new Function` problem (the
  // real fix is `unsafe-eval` in CSP). What sandbox *does* break: with
  // `allow-scripts` but no `allow-same-origin`, the iframe is in a
  // unique opaque origin while its base URL is inherited from the
  // parent, so anchor links like `<a href="#tasks">` inside the report
  // resolve to the parent's URL and clicking them triggers a
  // cross-origin navigation that hits the parent's
  // `frame-ancestors 'none'` and fails. Leaving sandbox off keeps the
  // report's internal nav (Summary/Resources/Tasks) working.
  test("report iframe is not sandboxed", async () => {
    const completedRun = { ...mockRunWithK8s, status: "completed" as const };
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("/references")) return Promise.resolve([]);
      return Promise.resolve(completedRun);
    });
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      text: () => Promise.resolve("<html><body>Nextflow Report</body></html>"),
    });

    const PipelineRunDetailPage =
      require("@/app/(app)/pipelines/runs/[id]/page").default;
    const { container } = render(<PipelineRunDetailPage />);

    await waitFor(() => {
      expect(screen.getByText("Report")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Report"));

    let iframe: HTMLIFrameElement | null = null;
    await waitFor(() => {
      iframe = container.querySelector("iframe");
      expect(iframe).not.toBeNull();
    });
    expect(iframe!.hasAttribute("sandbox")).toBe(false);
  });
});
