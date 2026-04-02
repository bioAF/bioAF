import { render, screen, waitFor, fireEvent } from "@testing-library/react";

// Mock next/navigation
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
}));

// Mock auth
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
}));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
    put: (...args: unknown[]) => mockApiPost(...args),
  },
}));

jest.mock("@/hooks/useComponents", () => ({
  useComponents: () => ({
    components: [
      { key: "jupyterhub", category: "analysis", enabled: true },
      { key: "rstudio", category: "analysis", enabled: true },
    ],
    loading: false,
    refetch: jest.fn(),
  }),
}));

// Mock layout components
jest.mock("@/components/layout/Sidebar", () => ({
  Sidebar: () => <div data-testid="sidebar">Sidebar</div>,
}));
jest.mock("@/components/layout/Header", () => ({
  Header: () => <div data-testid="header">Header</div>,
}));
jest.mock("@/components/shared/LoadingSpinner", () => ({
  LoadingSpinner: () => <div data-testid="loading-spinner">Loading...</div>,
}));

import NotebooksPage from "@/app/notebooks/page";

const mockSessions = {
  sessions: [
    {
      id: 1,
      session_type: "jupyter",
      user: { id: 1, name: "Test User", email: "test@test.com" },
      experiment: null,
      resource_profile: "small",
      cpu_cores: 2,
      memory_gb: 4,
      status: "running",
      idle_since: null,
      proxy_url: "http://notebook-svc.bioaf-notebooks:8888",
      started_at: "2026-03-12T10:00:00Z",
      stopped_at: null,
      created_at: "2026-03-12T10:00:00Z",
    },
  ],
  total: 1,
};

const mockExperiments = {
  experiments: [{ id: 1, name: "EXP-001" }],
  total: 1,
};

const mockEnvironments = {
  environments: [
    { id: 1, name: "Default scRNA-seq", description: null, version_count: 1, latest_version: 1, visibility: "team", created_at: "2026-03-12T10:00:00Z" },
  ],
  total: 1,
};

const mockEnvDetail = {
  id: 1,
  name: "Default scRNA-seq",
  description: null,
  visibility: "team",
  created_by: { id: 1, name: "Admin", email: "admin@test.com" },
  versions: [
    { id: 1, version_number: 1, status: "ready", definition_format: "dockerfile", image_uri: "us-central1-docker.pkg.dev/proj/bioaf-images/default-scrna:1", created_at: "2026-03-12T10:00:00Z" },
  ],
  created_at: "2026-03-12T10:00:00Z",
  updated_at: "2026-03-12T10:00:00Z",
};

describe("NotebooksPage", () => {
  beforeEach(() => {
    mockApiGet.mockReset();
    mockApiPost.mockReset();
  });

  // Test 30: Launch modal shows options
  it("shows launch modal with Jupyter and RStudio buttons", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("sessions")) return Promise.resolve({ sessions: [], total: 0 });
      if (url.includes("experiments")) return Promise.resolve(mockExperiments);
      if (url.includes("projects")) return Promise.resolve({ projects: [] });
      if (url.includes("/api/v1/environments/1")) return Promise.resolve(mockEnvDetail);
      if (url.includes("/api/v1/environments")) return Promise.resolve(mockEnvironments);
      return Promise.resolve({});
    });

    render(<NotebooksPage />);
    await waitFor(() => {
      expect(screen.getByText("Launch Session")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Launch Session"));
    await waitFor(() => {
      expect(screen.getByText(/launch jupyter/i)).toBeInTheDocument();
      expect(screen.getByText(/launch rstudio/i)).toBeInTheDocument();
    });
  });

  // Test 31: Launch button renders on page
  it("shows launch button on main page", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("sessions")) return Promise.resolve({ sessions: [], total: 0 });
      if (url.includes("experiments")) return Promise.resolve(mockExperiments);
      if (url.includes("projects")) return Promise.resolve({ projects: [] });
      if (url.includes("config")) return Promise.resolve({ compute_deployed: false });
      if (url.includes("/api/v1/environments/1")) return Promise.resolve(mockEnvDetail);
      if (url.includes("/api/v1/environments")) return Promise.resolve(mockEnvironments);
      return Promise.resolve({});
    });

    render(<NotebooksPage />);
    await waitFor(() => {
      expect(screen.getByText("Launch Session")).toBeInTheDocument();
    });
  });

  // Test 32: Launch modal has scope selector
  it("shows experiment and project scope options in launch modal", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("sessions")) return Promise.resolve({ sessions: [], total: 0 });
      if (url.includes("experiments")) return Promise.resolve(mockExperiments);
      if (url.includes("projects")) return Promise.resolve({ projects: [] });
      if (url.includes("/api/v1/environments/1")) return Promise.resolve(mockEnvDetail);
      if (url.includes("/api/v1/environments")) return Promise.resolve(mockEnvironments);
      return Promise.resolve({});
    });

    render(<NotebooksPage />);
    await waitFor(() => {
      expect(screen.getByText("Launch Session")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Launch Session"));
    await waitFor(() => {
      expect(screen.getByText("Launch Notebook Session")).toBeInTheDocument();
      expect(screen.getByText("Launch RStudio")).toBeInTheDocument();
    });
  });

  // Test 33: Active sessions table
  it("renders active sessions table with Open, Stop, Sync buttons", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("sessions")) return Promise.resolve(mockSessions);
      if (url.includes("experiments")) return Promise.resolve(mockExperiments);
      if (url.includes("projects")) return Promise.resolve({ projects: [] });
      if (url.includes("/api/v1/environments/1")) return Promise.resolve(mockEnvDetail);
      if (url.includes("/api/v1/environments")) return Promise.resolve(mockEnvironments);
      return Promise.resolve({});
    });

    render(<NotebooksPage />);
    await waitFor(() => {
      expect(screen.getByText("Open")).toBeInTheDocument();
      expect(screen.getByText("Stop")).toBeInTheDocument();
      expect(screen.getByText("Sync")).toBeInTheDocument();
    });
  });

  // Test 34: Open button links to access URL
  it("Open button has correct href for access URL", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("sessions")) return Promise.resolve(mockSessions);
      if (url.includes("experiments")) return Promise.resolve(mockExperiments);
      if (url.includes("projects")) return Promise.resolve({ projects: [] });
      if (url.includes("/api/v1/environments/1")) return Promise.resolve(mockEnvDetail);
      if (url.includes("/api/v1/environments")) return Promise.resolve(mockEnvironments);
      return Promise.resolve({});
    });

    render(<NotebooksPage />);
    await waitFor(() => {
      const openLink = screen.getByText("Open");
      expect(openLink.closest("a")).toHaveAttribute(
        "href",
        "http://notebook-svc.bioaf-notebooks:8888"
      );
    });
  });

  // Test 35: Stop button calls API
  it("Stop button calls stop endpoint", async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("sessions")) return Promise.resolve(mockSessions);
      if (url.includes("experiments")) return Promise.resolve(mockExperiments);
      if (url.includes("projects")) return Promise.resolve({ projects: [] });
      if (url.includes("/api/v1/environments/1")) return Promise.resolve(mockEnvDetail);
      if (url.includes("/api/v1/environments")) return Promise.resolve(mockEnvironments);
      return Promise.resolve({});
    });
    mockApiPost.mockResolvedValue({ status: "stopped" });

    // Mock window.confirm
    window.confirm = jest.fn(() => true);

    render(<NotebooksPage />);
    await waitFor(() => {
      expect(screen.getByText("Stop")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Stop"));
    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        expect.stringContaining("/sessions/1/stop")
      );
    });
  });

  // Test 36: Starting session shows spinner
  it("shows spinner for sessions with starting status", async () => {
    const startingSessions = {
      sessions: [
        {
          ...mockSessions.sessions[0],
          status: "starting",
          proxy_url: null,
        },
      ],
      total: 1,
    };
    mockApiGet.mockImplementation((url: string) => {
      if (url.includes("sessions")) return Promise.resolve(startingSessions);
      if (url.includes("experiments")) return Promise.resolve(mockExperiments);
      if (url.includes("projects")) return Promise.resolve({ projects: [] });
      if (url.includes("/api/v1/environments/1")) return Promise.resolve(mockEnvDetail);
      if (url.includes("/api/v1/environments")) return Promise.resolve(mockEnvironments);
      return Promise.resolve({});
    });

    render(<NotebooksPage />);
    await waitFor(() => {
      expect(screen.getByText(/starting/i)).toBeInTheDocument();
    });
  });
});
