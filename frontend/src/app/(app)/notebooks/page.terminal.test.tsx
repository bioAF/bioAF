/**
 * Terminal instructions in the notebook session details: shown for running or idle Jupyter and
 * RStudio sessions when the user's effective permissions allow it, and cleared when the session
 * stops. A server refusal for a stopped session re-reads the list.
 */
import { act, render, screen, fireEvent, waitFor, within } from "@/testing/renderWithProviders";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/notebooks",
}));
jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return { ...actual, api: { get: jest.fn(), post: jest.fn() } };
});
const perms = { canAccess: jest.fn() };
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: (...a: unknown[]) => perms.canAccess(...a), loading: false, failed: false }),
}));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: (flag: string) => flag === "ssh_exec", capabilities: {}, loading: false }),
}));
jest.mock("@/lib/auth", () => ({
  getToken: () => "fake-token",
  removeToken: jest.fn(),
  getCurrentUser: () => ({ role_name: "custom-role", email: "a@b.c" }),
  isAuthenticated: () => true,
}));
jest.mock("@/hooks/useComponents", () => ({
  useComponents: () => ({
    components: [
      { key: "jupyterhub", enabled: true },
      { key: "rstudio", enabled: true },
    ],
    loading: false,
    failed: false,
    refetch: jest.fn(),
  }),
}));

import NotebooksPage from "./page";
import { api, ApiError } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;
let sessions: unknown[];

function session(id: number, session_type: string, status: string) {
  return {
    id,
    session_type,
    status,
    user: { id: 1, name: `User ${id}`, email: "a@b.c" },
    experiment: null,
    project: null,
    cpu_cores: 2,
    memory_gb: 8,
    started_at: null,
    proxy_url: null,
    failure_reason: null,
  };
}

const connection = {
  command: "kubectl exec -it --context ctx -n bioaf-notebooks bioaf-notebook-5 --container notebook -- /bin/sh",
  setup_guide: "First-time setup",
  warning: "Commands you run and changes you make in this terminal are not tracked by bioAF.",
  target_type: "notebook_session",
  target_id: "bioaf-notebook-5",
  namespace: "bioaf-notebooks",
};

beforeEach(() => {
  perms.canAccess.mockReset();
  perms.canAccess.mockReturnValue(true);
  mockPost.mockReset();
  mockPost.mockResolvedValue(connection);
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/notebooks/sessions")) return Promise.resolve({ sessions });
    if (url.includes("build-status")) return Promise.resolve({ build_id: null, build_status: null, image_uri: null });
    if (url.includes("experiments")) return Promise.resolve({ experiments: [] });
    if (url.includes("projects")) return Promise.resolve({ projects: [] });
    if (url.includes("/api/v1/environments")) return Promise.resolve({ environments: [], total: 0 });
    return Promise.resolve({});
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.restoreAllMocks();
});

async function openDetails(id: number) {
  render(<NotebooksPage />);
  const row = await screen.findByText(`User ${id}`);
  fireEvent.click(within(row.closest("tr")!).getByText("Details"));
  return screen.findByRole("dialog");
}

test.each([
  ["jupyter", "running"],
  ["rstudio", "idle"],
])("%s %s session details offer terminal instructions", async (type, status) => {
  sessions = [session(5, type, status)];
  const dialog = await openDetails(5);

  expect(within(dialog).getByRole("heading", { name: "Terminal instructions" })).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Show instructions" }));

  expect(await within(dialog).findByTestId("connect-command")).toHaveTextContent(connection.command);
  expect(mockPost).toHaveBeenCalledWith("/api/sessions/5/connect", undefined, expect.anything());
  expect(perms.canAccess).toHaveBeenCalledWith("notebooks", "launch");
});

test.each([
  ["jupyter", "starting"],
  ["jupyter", "stopped"],
  ["ssh", "running"],
])("%s %s session details do not", async (type, status) => {
  sessions = [session(5, type, status)];
  const dialog = await openDetails(5);
  expect(within(dialog).queryByText("Terminal instructions")).not.toBeInTheDocument();
});

test("users without the launch grant do not see it", async () => {
  perms.canAccess.mockImplementation((r: string, a: string) => !(r === "notebooks" && a === "launch"));
  sessions = [session(5, "jupyter", "running")];
  const dialog = await openDetails(5);
  expect(within(dialog).queryByText("Terminal instructions")).not.toBeInTheDocument();
});

test("a stopped-session refusal re-reads the list, and the stopped session loses the control", async () => {
  sessions = [session(5, "jupyter", "running")];
  const dialog = await openDetails(5);
  const readsBefore = mockGet.mock.calls.filter(([u]) => String(u).includes("/notebooks/sessions")).length;

  sessions = [session(5, "jupyter", "stopped")];
  mockPost.mockRejectedValueOnce(
    new ApiError(409, "raw", "connection_unavailable", { reason: "not_running" }),
  );
  fireEvent.click(within(dialog).getByRole("button", { name: "Show instructions" }));

  await waitFor(() =>
    expect(mockGet.mock.calls.filter(([u]) => String(u).includes("/notebooks/sessions")).length).toBeGreaterThan(
      readsBefore,
    ),
  );
  await waitFor(() => expect(within(dialog).queryByText("Terminal instructions")).not.toBeInTheDocument());
});

test("expanded instructions disappear when polling finds the session stopped", async () => {
  jest.useFakeTimers();
  // A starting session keeps the page polling every 10 s.
  sessions = [session(5, "jupyter", "running"), session(6, "jupyter", "starting")];
  const dialog = await openDetails(5);
  fireEvent.click(within(dialog).getByRole("button", { name: "Show instructions" }));
  await within(dialog).findByTestId("connect-command");

  sessions = [session(5, "jupyter", "stopped"), session(6, "jupyter", "starting")];
  await act(async () => {
    jest.advanceTimersByTime(10_000);
  });

  await waitFor(() => expect(within(dialog).queryByTestId("connect-command")).not.toBeInTheDocument());
  expect(within(dialog).queryByText("Terminal instructions")).not.toBeInTheDocument();
  jest.useRealTimers();
});
