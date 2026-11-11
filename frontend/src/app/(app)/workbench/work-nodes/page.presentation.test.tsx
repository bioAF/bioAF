import { render, screen, waitFor, within, act } from "@/testing/renderWithProviders";
import { readFileSync } from "fs";
import { join } from "path";
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
      storage_uri: "gs://bucket/experiments/42/uploads/matrix.mtx.gz",
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

async function advanceToStep2(user: ReturnType<typeof userEvent.setup>) {
  setupApiMocks();
  render(<WorkNodesPage />);
  await openLaunchDialog(user);
  const profileGroup = screen.getByRole("group", { name: /machine profile/i });
  await user.click(within(profileGroup).getByRole("button", { name: /^Medium\b/i }));
  const envSelect = screen.getByRole("combobox", { name: /^Workbench template$/i });
  await user.selectOptions(envSelect, "1");
  await waitFor(() =>
    expect(screen.getByRole("option", { name: /v1\.1 \(ready\)/i })).toBeInTheDocument()
  );
  const scopeGroup = screen.getByRole("group", { name: /link to/i });
  const scopeDropdown = within(scopeGroup).getByRole("combobox");
  await user.selectOptions(scopeDropdown, "42");
  await user.click(screen.getByRole("button", { name: /^Next: Review$/i }));
}

/**
 * Work node presentation on the shared primitives: primary actions are the
 * app's Button (no separate indigo palette) at the launch footer's existing
 * hit target, feedback is announced by kind, and the repo form submits from
 * Enter once. Launch choices (profile cards, machine types, scope toggles)
 * keep their own controls and dimensions.
 */
describe("work node presentation", () => {
  it("launch footer actions keep their hit target on the shared Button", async () => {
    const user = userEvent.setup();
    setupApiMocks();
    render(<WorkNodesPage />);
    await openLaunchDialog(user);
    for (const name of [/^Next: Review$/i, /^Cancel$/i]) {
      const button = screen.getByRole("button", { name });
      expect(button.className).toMatch(/\bpy-2\.5\b/);
      expect(button).toHaveAttribute("type", "button");
    }
    expect(screen.getByRole("button", { name: /^Next: Review$/i }).className).toMatch(/bg-bioaf-600/);
  });

  it("a failed launch is announced as an alert with a Dismiss action", async () => {
    const user = userEvent.setup();
    const { ApiError } = jest.requireMock("@/lib/api") as { ApiError: typeof Error };
    mockPost.mockRejectedValueOnce(new (ApiError as never as new (s: number, m: string) => Error)(400, "Quota exceeded"));
    await advanceToStep2(user);
    await user.click(screen.getByRole("button", { name: /^Launch Work Node$/i }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Quota exceeded");
    expect(within(alert).getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
  });

  it("provisioning progress is a polite status, not an alert", async () => {
    const user = userEvent.setup();
    mockPost.mockReturnValue(new Promise(() => {}));
    await advanceToStep2(user);
    await user.click(screen.getByRole("button", { name: /^Launch Work Node$/i }));
    expect(screen.getAllByRole("status").some((s) => /provisioning work node/i.test(s.textContent ?? ""))).toBe(true);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("adds a repo from Enter, once", async () => {
    const user = userEvent.setup();
    setupApiMocks();
    mockPost.mockResolvedValue({});
    render(<WorkNodesPage />);
    await user.click(await screen.findByRole("button", { name: /GitHub Repos/ }));
    const form = screen.getByRole("form", { name: "Add GitHub repo" });
    await user.type(within(form).getByLabelText("Git SSH URL"), "git@github.com:lab/tools.git{Enter}");
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    expect(mockPost).toHaveBeenCalledWith("/api/v1/github-repos", { git_ssh_url: "git@github.com:lab/tools.git", display_name: null });
  });

  it("two repo submits before a re-render add one repo", async () => {
    const user = userEvent.setup();
    setupApiMocks();
    mockPost.mockReturnValue(new Promise(() => {}));
    render(<WorkNodesPage />);
    await user.click(await screen.findByRole("button", { name: /GitHub Repos/ }));
    const form = screen.getByRole("form", { name: "Add GitHub repo" });
    await user.type(within(form).getByLabelText("Git SSH URL"), "git@github.com:lab/tools.git");
    act(() => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(mockPost).toHaveBeenCalledTimes(1);
  });

  it("a rejected repo is announced", async () => {
    const user = userEvent.setup();
    setupApiMocks();
    const { ApiError } = jest.requireMock("@/lib/api") as { ApiError: typeof Error };
    mockPost.mockRejectedValueOnce(new (ApiError as never as new (s: number, m: string) => Error)(400, "That is not an SSH URL."));
    render(<WorkNodesPage />);
    await user.click(await screen.findByRole("button", { name: /GitHub Repos/ }));
    const form = screen.getByRole("form", { name: "Add GitHub repo" });
    await user.type(within(form).getByLabelText("Git SSH URL"), "https://x{Enter}");
    expect(await within(form.parentElement!).findByRole("alert")).toHaveTextContent("That is not an SSH URL.");
  });

  it("the page uses the app's brand ramp, not a separate indigo palette", () => {
    const source = readFileSync(join(__dirname, "page.tsx"), "utf8");
    expect(source.match(/indigo-\d+/g) ?? []).toEqual([]);
  });
});
