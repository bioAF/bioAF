/**
 * Tests 24-34: Infrastructure Components page (Phase 19).
 *
 * 24: Shows stack cards when no stack selected
 * 25: SLURM card is grayed out with Coming Soon
 * 26: Deploy button opens progress modal
 * 27: Shows operational view when deployed
 * 28: Cluster status card shows pool info
 * 29: Components use full names (no "K8s")
 * 30: Configure panel expands on click
 * 31: Teardown shows confirmation modal
 * 32: Teardown requires checkbox
 * 33: Enable calls toggle API
 * 34: Dependency enforcement shows message
 */

import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import InfraComponentsPage from "@/app/infrastructure/components/page";

// Mock API
const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
  },
  ApiError: class extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

// Mock auth
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getToken: () => "mock-token",
  getCurrentUser: () => ({ role: "admin", email: "test@test.com" }),
}));

// Mock router - return stable reference to avoid infinite useEffect loops
const mockPush = jest.fn();
const mockRouter = { push: mockPush };
jest.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  usePathname: () => "/infrastructure/components",
}));

// Mock next/link
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({
    href,
    children,
  }: {
    href: string;
    children: React.ReactNode;
  }) => <a href={href}>{children}</a>,
}));

// Mock EventSource for SSE
class MockEventSource {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close = jest.fn();
  constructor(public url: string) {}
}
(global as Record<string, unknown>).EventSource = MockEventSource;

function mockTfStatus(overrides = {}) {
  return {
    terraform_initialized: true,
    terraform_state_bucket: "gs://test",
    gcp_credentials_configured: true,
    active_run_id: null,
    active_run_status: null,
    ...overrides,
  };
}

function mockStackStatus(overrides = {}) {
  return {
    compute_stack: null,
    compute_deployed: false,
    storage_deployed: false,
    cluster: null,
    ...overrides,
  };
}

function mockStackComponents(components: Record<string, unknown>[] = []) {
  return {
    compute_stack: "kubernetes",
    compute_deployed: true,
    storage_deployed: true,
    components,
  };
}

function mockClusterStatus() {
  return {
    compute_stack: "kubernetes",
    compute_deployed: true,
    storage_deployed: true,
    cluster: {
      cluster_name: "bioaf-myorg",
      status: "RUNNING",
      node_count: 0,
      pipeline_pool: {
        name: "bioaf-pipelines",
        machine_type: "n2-highmem-8",
        min_nodes: 0,
        max_nodes: 20,
        current_nodes: 0,
        spot: true,
        status: "RUNNING",
      },
      interactive_pool: {
        name: "bioaf-interactive",
        machine_type: "n2-standard-4",
        min_nodes: 0,
        max_nodes: 5,
        current_nodes: 0,
        spot: false,
        status: "RUNNING",
      },
    },
  };
}

describe("InfraComponentsPage", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiPost.mockReset();
    mockPush.mockReset();
  });

  // Test 24: Stack cards when no stack
  it("shows stack selection cards when no stack is selected", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("terraform/status")) return Promise.resolve(mockTfStatus());
      if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
      if (url.includes("stack/status")) return Promise.resolve(mockStackStatus());
      if (url.includes("stack/components"))
        return Promise.resolve({ compute_stack: null, compute_deployed: false, storage_deployed: false, components: [] });
      return Promise.reject(new Error("Not found"));
    });

    await act(async () => {
      render(<InfraComponentsPage />);
    });

    await waitFor(() => {
      expect(screen.getByText(/Kubernetes \+ GCS/)).toBeInTheDocument();
    });
    expect(screen.getByText(/SLURM \+ NFS/)).toBeInTheDocument();
  });

  // Test 25: SLURM card grayed out
  it("shows SLURM card as grayed out with Coming Soon", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("terraform/status")) return Promise.resolve(mockTfStatus());
      if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
      if (url.includes("stack/status")) return Promise.resolve(mockStackStatus());
      if (url.includes("stack/components"))
        return Promise.resolve({ compute_stack: null, compute_deployed: false, storage_deployed: false, components: [] });
      return Promise.reject(new Error("Not found"));
    });

    await act(async () => {
      render(<InfraComponentsPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("Coming Soon")).toBeInTheDocument();
    });
  });

  // Test 26: Deploy button starts background deploy
  it("deploy button calls deploy-background endpoint", async () => {
    const idleProgress = {
      active: false,
      status: null,
      phase: null,
      resources_completed: 0,
      resources_total: 0,
      completed_resources: [],
      error_message: null,
      run_id: null,
    };

    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("deploy/progress")) return Promise.resolve(idleProgress);
      if (url.includes("terraform/status")) return Promise.resolve(mockTfStatus());
      if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
      if (url.includes("stack/status")) return Promise.resolve(mockStackStatus());
      if (url.includes("stack/components"))
        return Promise.resolve({ compute_stack: null, compute_deployed: false, storage_deployed: false, components: [] });
      return Promise.reject(new Error("Not found"));
    });
    mockApiPost.mockResolvedValue({ message: "Deployment started" });

    await act(async () => {
      render(<InfraComponentsPage />);
    });

    await waitFor(() => {
      expect(screen.getByText(/Kubernetes \+ GCS/)).toBeInTheDocument();
    });

    const deployButton = screen.getByRole("button", { name: /^Deploy$/i });
    await act(async () => {
      fireEvent.click(deployButton);
    });

    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        "/api/v1/infrastructure/stack/deploy-background",
        { stack_type: "kubernetes" },
      );
    });
  });

  // Test 27: Operational view when deployed
  it("shows operational view when compute is deployed", async () => {
    const components = [
      {
        key: "nextflow",
        name: "Nextflow",
        category: "pipeline_orchestration",
        description: "Pipeline orchestration",
        cost_estimate: "$0",
        dependencies: ["kubernetes_cluster"],
        status: "disabled",
        configurable: false,
      },
    ];

    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("terraform/status")) return Promise.resolve(mockTfStatus());
      if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
      if (url.includes("stack/status")) return Promise.resolve(mockClusterStatus());
      if (url.includes("stack/components")) return Promise.resolve(mockStackComponents(components));
      if (url.includes("storage/buckets")) return Promise.resolve({ buckets: [] });
      if (url.includes("cluster/config"))
        return Promise.resolve({
          k8s_pipeline_machine_type: "n2-highmem-8",
          k8s_pipeline_max_nodes: 20,
          k8s_pipeline_use_spot: true,
          k8s_interactive_machine_type: "n2-standard-4",
          k8s_interactive_max_nodes: 5,
        });
      return Promise.reject(new Error("Not found"));
    });

    await act(async () => {
      render(<InfraComponentsPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("Active")).toBeInTheDocument();
    });
    expect(screen.getByText(/Kubernetes \+ GCS/)).toBeInTheDocument();
    expect(screen.getByText("Nextflow")).toBeInTheDocument();
  });

  // Test 28: Cluster status card shows pool info
  it("cluster status card shows pool info", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("terraform/status")) return Promise.resolve(mockTfStatus());
      if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
      if (url.includes("stack/status")) return Promise.resolve(mockClusterStatus());
      if (url.includes("stack/components")) return Promise.resolve(mockStackComponents([]));
      if (url.includes("storage/buckets")) return Promise.resolve({ buckets: [] });
      if (url.includes("cluster/config"))
        return Promise.resolve({
          k8s_pipeline_machine_type: "n2-highmem-8",
          k8s_pipeline_max_nodes: 20,
          k8s_pipeline_use_spot: true,
          k8s_interactive_machine_type: "n2-standard-4",
          k8s_interactive_max_nodes: 5,
        });
      return Promise.reject(new Error("Not found"));
    });

    await act(async () => {
      render(<InfraComponentsPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("bioaf-myorg")).toBeInTheDocument();
    });
    expect(screen.getByText(/bioaf-pipelines/)).toBeInTheDocument();
    expect(screen.getByText(/bioaf-interactive/)).toBeInTheDocument();
    expect(screen.getByText(/n2-highmem-8/)).toBeInTheDocument();
    expect(screen.getByText(/n2-standard-4/)).toBeInTheDocument();
  });

  // Test 29: Full names, no "K8s"
  it("components use full names, no K8s abbreviation", async () => {
    const components = [
      {
        key: "nextflow",
        name: "Nextflow",
        category: "pipeline_orchestration",
        description: "Pipeline orchestration",
        cost_estimate: "$0",
        dependencies: ["kubernetes_cluster"],
        status: "disabled",
        configurable: false,
      },
      {
        key: "jupyterhub",
        name: "JupyterHub",
        category: "analysis",
        description: "Jupyter on Kubernetes",
        cost_estimate: "$50-$200",
        dependencies: ["kubernetes_cluster"],
        status: "disabled",
        configurable: true,
      },
    ];

    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("terraform/status")) return Promise.resolve(mockTfStatus());
      if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
      if (url.includes("stack/status")) return Promise.resolve(mockClusterStatus());
      if (url.includes("stack/components")) return Promise.resolve(mockStackComponents(components));
      if (url.includes("storage/buckets")) return Promise.resolve({ buckets: [] });
      if (url.includes("cluster/config"))
        return Promise.resolve({
          k8s_pipeline_machine_type: "n2-highmem-8",
          k8s_pipeline_max_nodes: 20,
          k8s_pipeline_use_spot: true,
          k8s_interactive_machine_type: "n2-standard-4",
          k8s_interactive_max_nodes: 5,
        });
      return Promise.reject(new Error("Not found"));
    });

    await act(async () => {
      render(<InfraComponentsPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("Nextflow")).toBeInTheDocument();
    });

    // Check that no text node contains "K8s"
    const body = document.body.textContent || "";
    expect(body).not.toContain("K8s");
  });

  // Test 30: Configure panel expands
  it("configure panel expands on click", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("terraform/status")) return Promise.resolve(mockTfStatus());
      if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
      if (url.includes("stack/status")) return Promise.resolve(mockClusterStatus());
      if (url.includes("stack/components")) return Promise.resolve(mockStackComponents([]));
      if (url.includes("storage/buckets")) return Promise.resolve({ buckets: [] });
      if (url.includes("cluster/config"))
        return Promise.resolve({
          k8s_pipeline_machine_type: "n2-highmem-8",
          k8s_pipeline_max_nodes: 20,
          k8s_pipeline_use_spot: true,
          k8s_interactive_machine_type: "n2-standard-4",
          k8s_interactive_max_nodes: 5,
        });
      return Promise.reject(new Error("Not found"));
    });

    await act(async () => {
      render(<InfraComponentsPage />);
    });

    await waitFor(() => {
      expect(screen.getByText(/Configure/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText(/Configure/i));

    await waitFor(() => {
      expect(screen.getByText(/Pipeline Pool/i)).toBeInTheDocument();
      expect(screen.getByText(/Interactive Pool/i)).toBeInTheDocument();
    });
  });

  // Test 31: Teardown shows confirmation modal
  it("teardown link shows confirmation modal", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("terraform/status")) return Promise.resolve(mockTfStatus());
      if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
      if (url.includes("stack/status")) return Promise.resolve(mockClusterStatus());
      if (url.includes("stack/components")) return Promise.resolve(mockStackComponents([]));
      if (url.includes("storage/buckets")) return Promise.resolve({ buckets: [] });
      if (url.includes("cluster/config"))
        return Promise.resolve({
          k8s_pipeline_machine_type: "n2-highmem-8",
          k8s_pipeline_max_nodes: 20,
          k8s_pipeline_use_spot: true,
          k8s_interactive_machine_type: "n2-standard-4",
          k8s_interactive_max_nodes: 5,
        });
      return Promise.reject(new Error("Not found"));
    });

    await act(async () => {
      render(<InfraComponentsPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("Teardown")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Teardown"));

    await waitFor(() => {
      expect(screen.getByText(/Teardown Compute Stack/i)).toBeInTheDocument();
      expect(
        screen.getByText(/terminate all running workloads/i)
      ).toBeInTheDocument();
    });
  });

  // Test 32: Teardown requires checkbox
  it("teardown button disabled until checkbox checked", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("terraform/status")) return Promise.resolve(mockTfStatus());
      if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
      if (url.includes("stack/status")) return Promise.resolve(mockClusterStatus());
      if (url.includes("stack/components")) return Promise.resolve(mockStackComponents([]));
      if (url.includes("storage/buckets")) return Promise.resolve({ buckets: [] });
      if (url.includes("cluster/config"))
        return Promise.resolve({
          k8s_pipeline_machine_type: "n2-highmem-8",
          k8s_pipeline_max_nodes: 20,
          k8s_pipeline_use_spot: true,
          k8s_interactive_machine_type: "n2-standard-4",
          k8s_interactive_max_nodes: 5,
        });
      return Promise.reject(new Error("Not found"));
    });

    await act(async () => {
      render(<InfraComponentsPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("Teardown")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Teardown"));

    await waitFor(() => {
      expect(screen.getByText(/Teardown Compute Stack/i)).toBeInTheDocument();
    });

    // Find all buttons with "Teardown" text - the confirm button in the modal
    const teardownButtons = screen.getAllByRole("button").filter(
      (btn) => btn.textContent === "Teardown"
    );
    // The last one is the confirm button inside the modal
    const confirmBtn = teardownButtons[teardownButtons.length - 1];
    expect(confirmBtn).toBeDisabled();

    // Check the checkbox
    const checkbox = screen.getByRole("checkbox");
    fireEvent.click(checkbox);

    // Now the button should be enabled
    expect(confirmBtn).toBeEnabled();
  });

  // Test 33: Enable calls toggle API
  it("enable button calls toggle API", async () => {
    const components = [
      {
        key: "nextflow",
        name: "Nextflow",
        category: "pipeline_orchestration",
        description: "Pipeline orchestration",
        cost_estimate: "$0",
        dependencies: ["kubernetes_cluster"],
        status: "disabled",
        configurable: false,
      },
    ];

    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("terraform/status")) return Promise.resolve(mockTfStatus());
      if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
      if (url.includes("stack/status")) return Promise.resolve(mockClusterStatus());
      if (url.includes("stack/components")) return Promise.resolve(mockStackComponents(components));
      if (url.includes("storage/buckets")) return Promise.resolve({ buckets: [] });
      if (url.includes("cluster/config"))
        return Promise.resolve({
          k8s_pipeline_machine_type: "n2-highmem-8",
          k8s_pipeline_max_nodes: 20,
          k8s_pipeline_use_spot: true,
          k8s_interactive_machine_type: "n2-standard-4",
          k8s_interactive_max_nodes: 5,
        });
      return Promise.reject(new Error("Not found"));
    });
    mockApiPost.mockResolvedValue({ component_key: "nextflow", enabled: true, status: "enabled" });

    await act(async () => {
      render(<InfraComponentsPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("Nextflow")).toBeInTheDocument();
    });

    const enableBtn = screen.getByRole("button", { name: /Enable/i });
    await act(async () => {
      fireEvent.click(enableBtn);
    });

    expect(mockApiPost).toHaveBeenCalledWith(
      "/api/v1/infrastructure/stack/components/nextflow/toggle"
    );
  });

  // Test 34: Dependency enforcement
  it("shows dependency warning when toggling without dependencies met", async () => {
    const components = [
      {
        key: "nextflow",
        name: "Nextflow",
        category: "pipeline_orchestration",
        description: "Pipeline orchestration",
        cost_estimate: "$0",
        dependencies: ["kubernetes_cluster"],
        status: "disabled",
        configurable: false,
      },
    ];

    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("terraform/status")) return Promise.resolve(mockTfStatus());
      if (url.includes("terraform/runs")) return Promise.resolve({ runs: [] });
      if (url.includes("stack/status")) return Promise.resolve(mockClusterStatus());
      if (url.includes("stack/components")) return Promise.resolve(mockStackComponents(components));
      if (url.includes("storage/buckets")) return Promise.resolve({ buckets: [] });
      if (url.includes("cluster/config"))
        return Promise.resolve({
          k8s_pipeline_machine_type: "n2-highmem-8",
          k8s_pipeline_max_nodes: 20,
          k8s_pipeline_use_spot: true,
          k8s_interactive_machine_type: "n2-standard-4",
          k8s_interactive_max_nodes: 5,
        });
      return Promise.reject(new Error("Not found"));
    });

    // Mock toggle returning a 400 error
    mockApiPost.mockRejectedValue(
      Object.assign(new Error("Dependency not met: kubernetes_cluster must be enabled first"), {
        status: 400,
      })
    );

    await act(async () => {
      render(<InfraComponentsPage />);
    });

    await waitFor(() => {
      expect(screen.getByText("Nextflow")).toBeInTheDocument();
    });

    const enableBtn = screen.getByRole("button", { name: /Enable/i });
    await act(async () => {
      fireEvent.click(enableBtn);
    });

    await waitFor(() => {
      expect(screen.getByText(/dependency/i)).toBeInTheDocument();
    });
  });
});
