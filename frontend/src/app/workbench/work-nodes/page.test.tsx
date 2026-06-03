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

const projects = { projects: [{ id: 7, name: "Project Alpha", description: null, code: "ALPHA" }], total: 1 };

function setupApiMocks(overrides: Partial<{ machineTypes: typeof machineTypes }> = {}) {
  const mts = overrides.machineTypes ?? machineTypes;
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/v1/work-nodes/sessions")) return Promise.resolve({ sessions: [] });
    if (url.includes("/api/v1/work-nodes/machine-types")) return Promise.resolve(mts);
    if (url.includes("/api/v1/github-repos")) return Promise.resolve({ repos: [] });
    if (url.includes("/api/v1/environments/1")) return Promise.resolve(envDetail);
    if (url.includes("/api/v1/environments")) return Promise.resolve(environments);
    if (url.includes("/api/projects")) return Promise.resolve(projects);
    if (url.includes("/api/experiments?project_id=")) return Promise.resolve({ experiments: [], total: 0 });
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
  await waitFor(() => expect(screen.getByText("Project Alpha")).toBeInTheDocument());
}

describe("WorkNodesPage launch dialog: 2-step shape", () => {
  it("step 1 shows all sections at once (no per-section Next buttons)", async () => {
    const user = userEvent.setup();
    setupApiMocks();
    render(<WorkNodesPage />);
    await openLaunchDialog(user);
    expect(screen.getByRole("heading", { name: /Select Project/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Select Environment/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Select GitHub Repos/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Machine Profile/i })).toBeInTheDocument();
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

  it("step 1 advances to step 2 (Review) after selections; step 2 has Back and Launch buttons", async () => {
    const user = userEvent.setup();
    setupApiMocks();
    render(<WorkNodesPage />);
    await openLaunchDialog(user);

    await user.click(screen.getByRole("button", { name: /Project Alpha/i }));
    const envSelect = await screen.findByRole("combobox", { name: "" });
    await user.selectOptions(envSelect, "1");
    await waitFor(() =>
      expect(screen.getByRole("option", { name: /v1\.1 \(ready\)/i })).toBeInTheDocument()
    );
    const profileGroup = screen.getByRole("group", { name: /machine profile/i });
    await user.click(within(profileGroup).getByRole("button", { name: /^Medium\b/i }));
    await user.click(screen.getByRole("button", { name: /^Next: Review$/i }));

    expect(screen.getByText(/^Review$/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Back$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Launch Work Node$/i })).toBeInTheDocument();
  });
});

async function advanceToStep2(user: ReturnType<typeof userEvent.setup>) {
  setupApiMocks();
  render(<WorkNodesPage />);
  await openLaunchDialog(user);
  await user.click(screen.getByRole("button", { name: /Project Alpha/i }));
  const envSelect = await screen.findByRole("combobox", { name: "" });
  await user.selectOptions(envSelect, "1");
  await waitFor(() =>
    expect(screen.getByRole("option", { name: /v1\.1 \(ready\)/i })).toBeInTheDocument()
  );
  const profileGroup = screen.getByRole("group", { name: /machine profile/i });
  await user.click(within(profileGroup).getByRole("button", { name: /^Medium\b/i }));
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
    mockPost.mockRejectedValueOnce(new (ApiError as never as new (s: number, m: string) => Error)(400, "Quota exceeded"));
    await advanceToStep2(user);

    await user.click(screen.getByRole("button", { name: /^Launch Work Node$/i }));
    await waitFor(() => expect(screen.getByText(/quota exceeded/i)).toBeInTheDocument());

    const dismiss = screen.getByRole("button", { name: /^Dismiss$/i });
    await user.click(dismiss);
    expect(screen.queryByText(/quota exceeded/i)).not.toBeInTheDocument();
  });
});
