/**
 * Launching a notebook session needs notebooks launch; the server refuses it otherwise. A role with
 * only notebooks view (the default bench role) still sees its sessions, and sees Launch Session and
 * Recreate disabled with the reason rather than a dialog whose launch the server will reject. The
 * same gate holds on both notebook-type buttons and on "Launch anyway", and the permission is read
 * again immediately before the launch is sent.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@/testing/renderWithProviders";

jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));

const mockGet = jest.fn();
const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
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

let grants = new Set<string>();
let permsLoading = false;
let permsFailed = false;
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    // Read at call time, so a grant withdrawn mid-flow is seen by the next check.
    canAccess: (resource: string, action: string) => grants.has(`${resource}:${action}`),
    loading: permsLoading,
    failed: permsFailed,
  }),
}));

import NotebooksPage from "./page";

const stoppedSession = {
  id: 9,
  session_type: "rstudio",
  user: { id: 1, name: "U", email: "u@x" },
  experiment: { id: 42, name: "Exp 42" },
  project: null,
  resource_profile: "medium",
  cpu_cores: 2,
  memory_gb: 8,
  status: "stopped",
  environment_version_id: 77,
  input_file_ids: [],
  started_at: "2026-01-01T00:00:00Z",
  stopped_at: "2026-01-01T01:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
};

function serve(sessions: unknown[] = [stoppedSession]) {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/api/v1/notebooks/sessions")) return Promise.resolve({ sessions, total: sessions.length });
    if (url.includes("/api/experiments")) return Promise.resolve({ experiments: [{ id: 42, name: "Exp 42" }] });
    if (url.includes("/api/projects")) return Promise.resolve({ projects: [] });
    if (url.includes("/api/v1/environments")) return Promise.resolve({ environments: [] });
    return Promise.resolve({});
  });
}

const LAUNCH_DENIED = /Your role does not include notebooks launch permission/;
const launches = () => mockPost.mock.calls.filter(([url]) => url === "/api/v1/notebooks/sessions");

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockPost.mockResolvedValue({});
  grants = new Set(["notebooks:view", "notebooks:launch"]);
  permsLoading = false;
  permsFailed = false;
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

function expectGated(button: HTMLElement, reason: RegExp) {
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute("title", expect.stringMatching(reason));
}

test("a role with only notebooks view sees its sessions, with launch and recreate disabled", async () => {
  grants = new Set(["notebooks:view"]);
  serve();
  render(<NotebooksPage />);
  const recreate = await screen.findByRole("button", { name: "Recreate" });
  expect(screen.getAllByText(/Exp 42/).length).toBeGreaterThan(0);
  expectGated(screen.getByRole("button", { name: "Launch Session" }), LAUNCH_DENIED);
  expectGated(recreate, LAUNCH_DENIED);

  fireEvent.click(screen.getByRole("button", { name: "Launch Session" }));
  fireEvent.click(recreate);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(launches()).toHaveLength(0);
});

test.each([
  ["loading", () => (permsLoading = true), /Checking your permissions/],
  ["unreadable", () => (permsFailed = true), /could not be confirmed/],
])("%s permissions leave launch disabled with the reason", async (_label, setup, reason) => {
  setup();
  serve();
  render(<NotebooksPage />);
  expectGated(await screen.findByRole("button", { name: "Launch Session" }), reason);
  expectGated(await screen.findByRole("button", { name: "Recreate" }), reason);
});

test("a custom role with notebooks launch can launch", async () => {
  grants = new Set(["notebooks:view", "notebooks:launch"]);
  serve([]);
  render(<NotebooksPage />);
  const launch = await screen.findByRole("button", { name: "Launch Session" });
  expect(launch).toBeEnabled();
  fireEvent.click(launch);
  const dialog = await screen.findByRole("dialog", { name: "Launch Notebook Session" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Launch Jupyter" }));
  const confirmDialog = await screen.findByRole("dialog", { name: "Launch without inputs?" });
  fireEvent.click(within(confirmDialog).getByRole("button", { name: /Launch anyway/ }));
  await waitFor(() => expect(launches()).toHaveLength(1));
});

test("launch withdrawn while the dialog is open disables both notebook types", async () => {
  serve([]);
  const view = render(<NotebooksPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Launch Session" }));
  const dialog = await screen.findByRole("dialog", { name: "Launch Notebook Session" });

  grants = new Set(["notebooks:view"]);
  view.rerender(<NotebooksPage />);
  expectGated(within(dialog).getByRole("button", { name: "Launch Jupyter" }), LAUNCH_DENIED);
  expectGated(within(dialog).getByRole("button", { name: "Launch RStudio" }), LAUNCH_DENIED);
});

test("launch withdrawn during the confirmation disables Launch anyway", async () => {
  serve();
  const view = render(<NotebooksPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Recreate" }));
  const confirmDialog = await screen.findByRole("dialog", { name: "Launch without inputs?" });

  grants = new Set(["notebooks:view"]);
  view.rerender(<NotebooksPage />);
  const anyway = within(confirmDialog).getByRole("button", { name: /Launch anyway/ });
  expectGated(anyway, LAUNCH_DENIED);
  fireEvent.click(anyway);
  expect(launches()).toHaveLength(0);
});

test("the permission is read again right before the launch is sent", async () => {
  serve();
  render(<NotebooksPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Recreate" }));
  const confirmDialog = await screen.findByRole("dialog", { name: "Launch without inputs?" });

  // Withdrawn with no render in between: the button still looks enabled, the send must not happen.
  grants = new Set(["notebooks:view"]);
  await act(async () => {
    fireEvent.click(within(confirmDialog).getByRole("button", { name: /Launch anyway/ }));
  });
  expect(launches()).toHaveLength(0);
});
