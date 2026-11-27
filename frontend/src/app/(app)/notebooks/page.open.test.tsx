/**
 * Plan 03.5A: a protected session (address "/s/{id}/") opens through the notebook gateway with a
 * single-use ticket; it is never linked directly. Legacy sessions keep their recorded link.
 */
import { render, screen, fireEvent, waitFor, within } from "@/testing/renderWithProviders";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/notebooks",
}));
jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return { ...actual, api: { get: jest.fn(), post: jest.fn() } };
});
afterEach(() => jest.restoreAllMocks());
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
jest.mock("@/lib/notebookAccess", () => ({
  ...jest.requireActual("@/lib/notebookAccess"),
  openProtectedSession: jest.fn(),
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
import { openProtectedSession } from "@/lib/notebookAccess";
const mockOpen = openProtectedSession as jest.Mock;
const { toastMock } = jest.requireMock("@/components/shared/Toast");
import { api } from "@/lib/api";

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

beforeEach(() => {
  perms.canAccess.mockReturnValue(true);
  mockOpen.mockReset();
  toastMock.error.mockClear();
  jest.spyOn(console, "error").mockImplementation(() => {});
  mockOpen.mockResolvedValue(undefined);
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/notebooks/sessions")) return Promise.resolve({ sessions });
    if (url.includes("build-status")) return Promise.resolve({ build_id: null, build_status: null, image_uri: null });
    if (url.includes("experiments")) return Promise.resolve({ experiments: [] });
    if (url.includes("projects")) return Promise.resolve({ projects: [] });
    if (url.includes("/api/v1/environments")) return Promise.resolve({ environments: [], total: 0 });
    return Promise.resolve({});
  });
});

test("Open on a protected session goes through the gateway, from the row and the details", async () => {
  sessions = [{ ...session(41, "jupyter", "running"), proxy_url: "/s/41/" }];
  render(<NotebooksPage />);
  const row = (await screen.findByText("User 41")).closest("tr")!;
  expect(within(row).queryByRole("link", { name: "Open" })).not.toBeInTheDocument();
  fireEvent.click(within(row).getByRole("button", { name: "Open" }));
  await waitFor(() => expect(mockOpen).toHaveBeenCalledWith(41));

  fireEvent.click(within(row).getByText("Details"));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Open" }));
  await waitFor(() => expect(mockOpen).toHaveBeenCalledTimes(2));
});

test("a refused Open shows the reason", async () => {
  sessions = [{ ...session(41, "jupyter", "running"), proxy_url: "/s/41/" }];
  mockOpen.mockRejectedValue(new Error("Only the person who started this session can open it."));
  render(<NotebooksPage />);
  const row = (await screen.findByText("User 41")).closest("tr")!;
  fireEvent.click(within(row).getByRole("button", { name: "Open" }));
  await waitFor(() =>
    expect(toastMock.error).toHaveBeenCalledWith("Only the person who started this session can open it."),
  );
});

test("a legacy session keeps its recorded link", async () => {
  sessions = [{ ...session(7, "jupyter", "running"), proxy_url: "http://34.1.2.3:8888" }];
  render(<NotebooksPage />);
  const row = (await screen.findByText("User 7")).closest("tr")!;
  expect(within(row).getByRole("link", { name: "Open" })).toHaveAttribute("href", "http://34.1.2.3:8888");
  expect(mockOpen).not.toHaveBeenCalled();
});
