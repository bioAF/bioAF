import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import WorkNodesPage from "./page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/workbench/work-nodes",
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
    delete: jest.fn(),
  },
  ApiError: class ApiError extends Error {
    constructor(public status: number, message: string) {
      super(message);
    }
  },
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getToken: () => "fake-token",
  removeToken: jest.fn(),
  getCurrentUser: () => ({ role_name: "admin", email: "admin@test.com" }),
}));

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: () => true,
    roleName: "admin",
    loading: false,
    permissions: new Set(["work_nodes:launch", "work_nodes:view", "work_nodes:stop"]),
  }),
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

const machineTypes = [
  { name: "e2-standard-4", category: "standard", cpu: 4, memory_gb: 16, gpu: null, description: "Light analysis" },
  { name: "e2-standard-8", category: "standard", cpu: 8, memory_gb: 32, gpu: null, description: "General" },
  { name: "e2-highmem-8", category: "high-memory", cpu: 8, memory_gb: 64, gpu: null, description: "Larger datasets" },
  { name: "n2-highmem-16", category: "high-memory", cpu: 16, memory_gb: 128, gpu: null, description: "Multi-sample" },
  { name: "n2-highmem-32", category: "high-memory", cpu: 32, memory_gb: 256, gpu: null, description: "Extreme" },
  { name: "n1-standard-8-nvidia-tesla-t4", category: "gpu", cpu: 8, memory_gb: 30, gpu: "T4", description: "Entry GPU" },
];

const environments = {
  environments: [
    {
      id: 1,
      name: "Default WN env",
      description: null,
      version_count: 1,
      latest_version: { id: 11, version_number: 1, status: "ready", image_uri: "img:1", build_number: 1 },
      visibility: "team",
      created_at: "2026-03-12T10:00:00Z",
    },
  ],
  total: 1,
};

const envDetail = {
  id: 1,
  name: "Default WN env",
  description: null,
  visibility: "team",
  created_by: { id: 1, name: "Admin", email: "admin@test.com" },
  versions: [
    { id: 11, version_number: 1, build_number: 1, status: "ready", definition_format: "dockerfile", image_uri: "img:1", created_at: "2026-03-12T10:00:00Z" },
  ],
  created_at: "2026-03-12T10:00:00Z",
  updated_at: "2026-03-12T10:00:00Z",
};

const projects = {
  projects: [{ id: 7, name: "Project Alpha", description: null, code: "ALPHA" }],
  total: 1,
};

const experiments = {
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

const experimentFiles = {
  files: [
    {
      id: 100,
      filename: "matrix.mtx.gz",
      gcs_uri: "gs://bucket/experiments/42/uploads/matrix.mtx.gz",
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

function setupApiMocks(overrides: Partial<{ machineTypes: typeof machineTypes }> = {}) {
  const mts = overrides.machineTypes ?? machineTypes;
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/v1/work-nodes/sessions")) return Promise.resolve({ sessions: [] });
    if (url.includes("/api/v1/work-nodes/machine-types")) return Promise.resolve(mts);
    if (url.includes("/api/v1/github-repos")) return Promise.resolve({ repos: [] });
    if (url.includes("/api/v1/environments/1")) return Promise.resolve(envDetail);
    if (url.includes("/api/v1/environments")) return Promise.resolve(environments);
    if (url.includes("/api/projects")) return Promise.resolve(projects);
    if (url.includes("/api/experiments/42/files")) return Promise.resolve(experimentFiles);
    if (url.includes("/api/experiments/42/samples")) return Promise.resolve({ samples: [] });
    if (url.includes("/api/experiments")) return Promise.resolve(experiments);
    return Promise.resolve({});
  });
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
});

async function openLaunchDialog(user: ReturnType<typeof userEvent.setup>) {
  const openButton = await screen.findByRole("button", { name: /^New Work Node$/i });
  await user.click(openButton);
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: /Machine Profile/i })).toBeInTheDocument()
  );
}

describe("WorkNodesPage launch dialog: shape and ordering", () => {
  it("step 1 sections appear in order: Machine Profile, Environment, Link to, GitHub Repos", async () => {
    const user = userEvent.setup();
    setupApiMocks();
    render(<WorkNodesPage />);
    await openLaunchDialog(user);
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual([
      expect.stringMatching(/Machine Profile/i),
      expect.stringMatching(/Select Environment/i),
      expect.stringMatching(/Link to/i),
      expect.stringMatching(/GitHub Repos/i),
    ]);
    expect(screen.getByRole("button", { name: /^Next: Review$/i })).toBeInTheDocument();
  });

  it("renders 7 curated profile cards (Small, Medium, Large, X Large, XX Large, GPU, High memory)", async () => {
    const user = userEvent.setup();
    setupApiMocks();
    render(<WorkNodesPage />);
    await openLaunchDialog(user);
    const profileGroup = screen.getByRole("group", { name: /machine profile/i });
    expect(within(profileGroup).getByRole("button", { name: /^Small\b/i })).toBeInTheDocument();
    expect(within(profileGroup).getByRole("button", { name: /^Medium\b/i })).toBeInTheDocument();
    expect(within(profileGroup).getByRole("button", { name: /^Large\b/i })).toBeInTheDocument();
    expect(within(profileGroup).getByRole("button", { name: /^X Large\b/i })).toBeInTheDocument();
    expect(within(profileGroup).getByRole("button", { name: /^XX Large\b/i })).toBeInTheDocument();
    expect(within(profileGroup).getByRole("button", { name: /^GPU \(default\)/i })).toBeInTheDocument();
    expect(within(profileGroup).getByRole("button", { name: /^High memory \(default\)/i })).toBeInTheDocument();
  });

  it("Advanced expander starts collapsed and reveals the raw machine list when clicked", async () => {
    const user = userEvent.setup();
    setupApiMocks();
    render(<WorkNodesPage />);
    await openLaunchDialog(user);
    expect(screen.queryByRole("button", { name: /n1-standard-8-nvidia-tesla-t4/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Advanced/i }));
    expect(screen.getByRole("button", { name: /n1-standard-8-nvidia-tesla-t4/i })).toBeInTheDocument();
  });

  it("GPU profile card is disabled when catalog has no GPU machine types", async () => {
    const user = userEvent.setup();
    setupApiMocks({ machineTypes: machineTypes.filter((m) => m.category !== "gpu") });
    render(<WorkNodesPage />);
    await openLaunchDialog(user);
    const profileGroup = screen.getByRole("group", { name: /machine profile/i });
    const gpuBtn = within(profileGroup).getByRole("button", { name: /^GPU \(default\)/i });
    expect(gpuBtn).toBeDisabled();
  });
});

describe("WorkNodesPage scope toggle (Experiment / Project)", () => {
  it("renders Experiment and Project pill buttons, with Experiment active by default", async () => {
    const user = userEvent.setup();
    setupApiMocks();
    render(<WorkNodesPage />);
    await openLaunchDialog(user);
    const scopeGroup = screen.getByRole("group", { name: /link to/i });
    const expPill = within(scopeGroup).getByRole("button", { name: /^Experiment$/i });
    const projPill = within(scopeGroup).getByRole("button", { name: /^Project$/i });
    expect(expPill).toHaveAttribute("aria-pressed", "true");
    expect(projPill).toHaveAttribute("aria-pressed", "false");
  });

  it("Experiment scope shows experiments in the dropdown; Project scope shows projects", async () => {
    const user = userEvent.setup();
    setupApiMocks();
    render(<WorkNodesPage />);
    await openLaunchDialog(user);
    const scopeGroup = screen.getByRole("group", { name: /link to/i });
    expect(within(scopeGroup).getByRole("option", { name: /Experiment Beta/i })).toBeInTheDocument();
    await user.click(within(scopeGroup).getByRole("button", { name: /^Project$/i }));
    expect(within(scopeGroup).getByRole("option", { name: /Project Alpha/i })).toBeInTheDocument();
  });

  it("auto-shows the file picker (chip bar) when an experiment is selected; no 'Select files' button needed", async () => {
    const user = userEvent.setup();
    setupApiMocks();
    render(<WorkNodesPage />);
    await openLaunchDialog(user);
    expect(screen.queryByRole("group", { name: /file type filters/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /select files/i })).not.toBeInTheDocument();
    const scopeGroup = screen.getByRole("group", { name: /link to/i });
    const dropdown = within(scopeGroup).getByRole("combobox");
    await user.selectOptions(dropdown, "42");
    await waitFor(() =>
      expect(screen.getByRole("group", { name: /file type filters/i })).toBeInTheDocument()
    );
    expect(screen.queryByRole("button", { name: /select files/i })).not.toBeInTheDocument();
  });
});

describe("WorkNodesPage launch button posts project_id correctly", () => {
  it("derives project_id from selected experiment", async () => {
    const user = userEvent.setup();
    setupApiMocks();
    mockPost.mockResolvedValue({});
    render(<WorkNodesPage />);
    await openLaunchDialog(user);

    const profileGroup = screen.getByRole("group", { name: /machine profile/i });
    await user.click(within(profileGroup).getByRole("button", { name: /^Medium\b/i }));

    const envSelect = screen.getByRole("combobox", { name: /^Environment$/i });
    await user.selectOptions(envSelect, "1");
    await waitFor(() =>
      expect(screen.getByRole("option", { name: /v1\.1 \(ready\)/i })).toBeInTheDocument()
    );

    const scopeGroup = screen.getByRole("group", { name: /link to/i });
    const scopeDropdown = within(scopeGroup).getByRole("combobox");
    await user.selectOptions(scopeDropdown, "42");

    await user.click(screen.getByRole("button", { name: /^Next: Review$/i }));
    await user.click(screen.getByRole("button", { name: /^Launch Work Node$/i }));

    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    const [, body] = mockPost.mock.calls[0];
    expect(body.project_id).toBe(7);
    expect(body.machine_type).toBe("e2-standard-8");
  });

  it("uses selected project_id directly when scope is Project", async () => {
    const user = userEvent.setup();
    setupApiMocks();
    mockPost.mockResolvedValue({});
    render(<WorkNodesPage />);
    await openLaunchDialog(user);

    const profileGroup = screen.getByRole("group", { name: /machine profile/i });
    await user.click(within(profileGroup).getByRole("button", { name: /^Medium\b/i }));

    const envSelect = screen.getByRole("combobox", { name: /^Environment$/i });
    await user.selectOptions(envSelect, "1");
    await waitFor(() =>
      expect(screen.getByRole("option", { name: /v1\.1 \(ready\)/i })).toBeInTheDocument()
    );

    const scopeGroup = screen.getByRole("group", { name: /link to/i });
    await user.click(within(scopeGroup).getByRole("button", { name: /^Project$/i }));
    const scopeDropdown = within(scopeGroup).getByRole("combobox");
    await user.selectOptions(scopeDropdown, "7");

    await user.click(screen.getByRole("button", { name: /^Next: Review$/i }));
    await user.click(screen.getByRole("button", { name: /^Launch Work Node$/i }));

    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    const [, body] = mockPost.mock.calls[0];
    expect(body.project_id).toBe(7);
  });
});

async function advanceToStep2(user: ReturnType<typeof userEvent.setup>) {
  setupApiMocks();
  render(<WorkNodesPage />);
  await openLaunchDialog(user);
  const profileGroup = screen.getByRole("group", { name: /machine profile/i });
  await user.click(within(profileGroup).getByRole("button", { name: /^Medium\b/i }));
  const envSelect = screen.getByRole("combobox", { name: /^Environment$/i });
  await user.selectOptions(envSelect, "1");
  await waitFor(() =>
    expect(screen.getByRole("option", { name: /v1\.1 \(ready\)/i })).toBeInTheDocument()
  );
  const scopeGroup = screen.getByRole("group", { name: /link to/i });
  const scopeDropdown = within(scopeGroup).getByRole("combobox");
  await user.selectOptions(scopeDropdown, "42");
  await user.click(screen.getByRole("button", { name: /^Next: Review$/i }));
}

describe("WorkNodesPage launch click feedback", () => {
  it("closes the dialog immediately when Launch Work Node is clicked, before POST resolves", async () => {
    const user = userEvent.setup();
    let resolvePost!: (value: unknown) => void;
    const postPromise = new Promise((resolve) => {
      resolvePost = resolve;
    });
    mockPost.mockReturnValue(postPromise);
    await advanceToStep2(user);

    await user.click(screen.getByRole("button", { name: /^Launch Work Node$/i }));

    expect(screen.queryByRole("heading", { name: /^Review$/i })).not.toBeInTheDocument();

    resolvePost({});
  });

  it("shows a provisioning banner after the dialog closes", async () => {
    const user = userEvent.setup();
    let resolvePost!: (value: unknown) => void;
    const postPromise = new Promise((resolve) => {
      resolvePost = resolve;
    });
    mockPost.mockReturnValue(postPromise);
    await advanceToStep2(user);

    await user.click(screen.getByRole("button", { name: /^Launch Work Node$/i }));
    expect(screen.getByText(/provisioning work node/i)).toBeInTheDocument();

    resolvePost({});
  });

  it("removes the provisioning banner after the POST resolves successfully and reloads the node list", async () => {
    const user = userEvent.setup();
    let resolvePost!: (value: unknown) => void;
    const postPromise = new Promise((resolve) => {
      resolvePost = resolve;
    });
    mockPost.mockReturnValue(postPromise);
    await advanceToStep2(user);

    mockGet.mockClear();
    await user.click(screen.getByRole("button", { name: /^Launch Work Node$/i }));
    expect(screen.getByText(/provisioning work node/i)).toBeInTheDocument();

    resolvePost({});
    await waitFor(() =>
      expect(screen.queryByText(/provisioning work node/i)).not.toBeInTheDocument()
    );
    expect(
      mockGet.mock.calls.some((call) => String(call[0]).includes("/api/v1/work-nodes/sessions"))
    ).toBe(true);
  });

  it("shows an error banner with the message and a Dismiss button when POST fails", async () => {
    const user = userEvent.setup();
    const { ApiError } = jest.requireMock("@/lib/api") as { ApiError: typeof Error };
    mockPost.mockRejectedValueOnce(
      new (ApiError as never as new (s: number, m: string) => Error)(400, "Quota exceeded")
    );
    await advanceToStep2(user);

    await user.click(screen.getByRole("button", { name: /^Launch Work Node$/i }));
    await waitFor(() => expect(screen.getByText(/quota exceeded/i)).toBeInTheDocument());

    const dismiss = screen.getByRole("button", { name: /^Dismiss$/i });
    await user.click(dismiss);
    expect(screen.queryByText(/quota exceeded/i)).not.toBeInTheDocument();
  });
});
