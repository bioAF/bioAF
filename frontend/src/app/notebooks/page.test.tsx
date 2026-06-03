import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import NotebooksPage from "./page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/notebooks",
}));

jest.mock("next/link", () => {
  return function MockLink({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) {
    return <a href={href}>{children}</a>;
  };
});

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
  },
}));

jest.mock("@/lib/auth", () => ({
  getToken: () => "fake-token",
  removeToken: jest.fn(),
  getCurrentUser: () => ({ role_name: "admin", email: "admin@test.com" }),
  isAuthenticated: () => true,
}));

const mockComponents = jest.fn();
jest.mock("@/hooks/useComponents", () => ({
  useComponents: () => mockComponents(),
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

function makeComponent(key: string, category: string, enabled: boolean) {
  return { key, name: key, description: "", category, enabled, status: enabled ? "ready" : "disabled", config: {}, dependencies: [], estimated_monthly_cost: "", updated_at: null };
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockComponents.mockReturnValue({
    components: [
      makeComponent("jupyterhub", "analysis", true),
      makeComponent("rstudio", "analysis", true),
    ],
    loading: false,
    refetch: jest.fn(),
  });
});

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

function setupMocks(buildStatus: {
  build_id: string | null;
  build_status: string | null;
  image_uri: string | null;
}) {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("sessions")) return Promise.resolve({ sessions: [] });
    if (url.includes("experiments")) return Promise.resolve({ experiments: [] });
    if (url.includes("projects")) return Promise.resolve({ projects: [] });
    if (url.includes("build-status")) return Promise.resolve(buildStatus);
    if (url.includes("/api/v1/environments/1")) return Promise.resolve(mockEnvDetail);
    if (url.includes("/api/v1/environments")) return Promise.resolve(mockEnvironments);
    return Promise.resolve({});
  });
}

describe("NotebooksPage build status", () => {
  test("shows building banner when image build is in progress", async () => {
    setupMocks({
      build_id: "abc-123",
      build_status: "WORKING",
      image_uri: null,
    });

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Notebook image is building")).toBeInTheDocument();
    });
    expect(screen.getByText(/abc-123/)).toBeInTheDocument();
  });

  test("shows failure banner when last build failed", async () => {
    setupMocks({
      build_id: "fail-456",
      build_status: "FAILURE",
      image_uri: null,
    });

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Notebook image build failed")).toBeInTheDocument();
    });
  });

  test("shows launch button when build succeeded", async () => {
    setupMocks({
      build_id: "ok-789",
      build_status: "SUCCESS",
      image_uri: "us-central1-docker.pkg.dev/proj/bioaf-images/bioaf-scrna:latest",
    });

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Launch Session")).toBeInTheDocument();
    });
    expect(screen.queryByText("Notebook image is building")).not.toBeInTheDocument();
    expect(screen.queryByText("Notebook image build failed")).not.toBeInTheDocument();
  });

  test("shows launch button when no build exists", async () => {
    setupMocks({
      build_id: null,
      build_status: null,
      image_uri: null,
    });

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Launch Session")).toBeInTheDocument();
    });
    expect(screen.queryByText("Notebook image is building")).not.toBeInTheDocument();
    expect(screen.queryByText("Notebook image build failed")).not.toBeInTheDocument();
  });
});

describe("NotebooksPage launch modal", () => {
  test("shows launch modal with options when button clicked", async () => {
    setupMocks({ build_id: null, build_status: null, image_uri: null });

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Launch Session")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Launch Session"));

    await waitFor(() => {
      expect(screen.getByText("Launch Notebook Session")).toBeInTheDocument();
      expect(screen.getByText("Launch RStudio")).toBeInTheDocument();
      expect(screen.getByText("Launch Jupyter")).toBeInTheDocument();
    });
  });

  test("offers the full resource profile ladder up to 128GB", async () => {
    setupMocks({ build_id: null, build_status: null, image_uri: null });

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Launch Session")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Launch Session"));

    await waitFor(() => {
      expect(screen.getByText("Launch Notebook Session")).toBeInTheDocument();
    });

    // All five tiers are shown in the picker (Small through XX Large).
    for (const label of ["Small", "Medium", "Large", "X Large", "XX Large"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("8 CPU / 32 GB RAM")).toBeInTheDocument();
    expect(screen.getByText("16 CPU / 64 GB RAM")).toBeInTheDocument();
    expect(screen.getByText("16 CPU / 128 GB RAM")).toBeInTheDocument();
  });

  test("shows error in modal on launch failure", async () => {
    setupMocks({ build_id: null, build_status: null, image_uri: null });
    mockPost.mockRejectedValue(new Error("The notebook image is currently building."));

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Launch Session")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Launch Session"));

    await waitFor(() => {
      expect(screen.getByText("Launch RStudio")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Launch RStudio"));

    await waitFor(() => {
      expect(screen.getByText("The notebook image is currently building.")).toBeInTheDocument();
    });
  });
});

describe("NotebooksPage env picker filter", () => {
  test("requests environments filtered by type=notebook", async () => {
    setupMocks({ build_id: null, build_status: null, image_uri: null });

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(
        expect.stringMatching(/^\/api\/v1\/environments\?type=notebook$/)
      );
    });
    expect(mockGet).not.toHaveBeenCalledWith("/api/v1/environments");
  });
});

describe("NotebooksPage launch button component gating", () => {
  test("hides Launch RStudio when rstudio is not enabled", async () => {
    mockComponents.mockReturnValue({
      components: [
        makeComponent("jupyterhub", "analysis", true),
        makeComponent("rstudio", "analysis", false),
      ],
      loading: false,
      refetch: jest.fn(),
    });
    setupMocks({ build_id: null, build_status: null, image_uri: null });

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Launch Session")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Launch Session"));

    await waitFor(() => {
      expect(screen.getByText("Launch Jupyter")).toBeInTheDocument();
    });
    expect(screen.queryByText("Launch RStudio")).not.toBeInTheDocument();
  });

  test("hides Launch Jupyter when jupyterhub is not enabled", async () => {
    mockComponents.mockReturnValue({
      components: [
        makeComponent("jupyterhub", "analysis", false),
        makeComponent("rstudio", "analysis", true),
      ],
      loading: false,
      refetch: jest.fn(),
    });
    setupMocks({ build_id: null, build_status: null, image_uri: null });

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Launch Session")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Launch Session"));

    await waitFor(() => {
      expect(screen.getByText("Launch RStudio")).toBeInTheDocument();
    });
    expect(screen.queryByText("Launch Jupyter")).not.toBeInTheDocument();
  });

  test("shows both buttons when both components are enabled", async () => {
    setupMocks({ build_id: null, build_status: null, image_uri: null });

    render(<NotebooksPage />);

    await waitFor(() => {
      expect(screen.getByText("Launch Session")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Launch Session"));

    await waitFor(() => {
      expect(screen.getByText("Launch RStudio")).toBeInTheDocument();
      expect(screen.getByText("Launch Jupyter")).toBeInTheDocument();
    });
  });
});

describe("NotebooksPage file picker auto-shows when experiment selected", () => {
  test("renders FileTreeSelector inline after selecting an experiment, no 'Select files' button", async () => {
    const user = userEvent.setup();

    const experimentWithFiles = {
      experiments: [
        {
          id: 42,
          name: "Experiment Beta",
          code: "BETA",
          project: { id: 7, name: "Project Alpha", code: "ALPHA" },
        },
      ],
      total: 1,
    };
    const filesResponse = {
      files: [
        {
          id: 100,
          filename: "matrix.mtx.gz",
          gcs_uri: "gs://b/x/matrix.mtx.gz",
          size_bytes: 1024,
          md5_checksum: null,
          file_type: "count_matrix",
          tags: [],
          uploader: null,
          project_id: 7,
          experiment_id: 42,
          sample_ids: [],
          source_type: "upload",
          source_pipeline_run_id: null,
          source_notebook_session_id: null,
          storage_deleted: false,
          upload_timestamp: "2026-01-01T00:00:00Z",
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 500,
    };

    mockGet.mockImplementation((url: string) => {
      if (url.includes("/api/v1/notebooks/sessions")) return Promise.resolve({ sessions: [] });
      if (url.includes("/api/experiments/42/files")) return Promise.resolve(filesResponse);
      if (url.includes("/api/experiments/42/samples")) return Promise.resolve({ samples: [] });
      if (url.includes("/api/experiments")) return Promise.resolve(experimentWithFiles);
      if (url.includes("/api/projects")) return Promise.resolve({ projects: [], total: 0 });
      if (url.includes("build-status"))
        return Promise.resolve({ build_id: null, build_status: null, image_uri: null });
      if (url.includes("/api/v1/environments/1")) return Promise.resolve(mockEnvDetail);
      if (url.includes("/api/v1/environments")) return Promise.resolve(mockEnvironments);
      if (url.includes("/api/v1/notebooks/resource-profiles"))
        return Promise.resolve({ pool_machine_type: "n2-standard-8", profiles: [] });
      return Promise.resolve({});
    });

    render(<NotebooksPage />);
    await waitFor(() => expect(screen.getByText("Launch Session")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Launch Session"));
    await waitFor(() => expect(screen.getByText("Launch Notebook Session")).toBeInTheDocument());

    expect(screen.queryByRole("button", { name: /select files/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: /file type filters/i })).not.toBeInTheDocument();

    const expSelect = await screen.findByRole("combobox", { name: /^Experiment$/i });
    await user.selectOptions(expSelect, "42");

    await waitFor(() =>
      expect(screen.getByRole("group", { name: /file type filters/i })).toBeInTheDocument()
    );
    expect(screen.queryByRole("button", { name: /select files/i })).not.toBeInTheDocument();
  });
});
