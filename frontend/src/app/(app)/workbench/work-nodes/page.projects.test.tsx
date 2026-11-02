/**
 * Both ways of reaching the launch form load the project list: opening the
 * dialog, and the refill when recreating a node. A role that may not list
 * projects keeps every other option on both; a failed load stays visible.
 */
import { render, screen, waitFor, fireEvent } from "@/testing/renderWithProviders";
import WorkNodesPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "bench" }),
  getToken: () => "t",
}));
const routerMock = { push: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  usePathname: () => "/workbench/work-nodes",
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => true, capabilities: {}, loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn(), delete: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;
const { toastMock } = jest.requireMock("@/components/shared/Toast");

const NODE = {
  id: 1,
  status: "stopped",
  project_id: 9,
  experiment_id: null,
  environment_version_id: null,
  machine_type: "e2-standard-4",
  input_file_ids: [],
  github_repo_ids: [],
  created_at: "2026-05-01T00:00:00Z",
  user: { id: 1, name: "A", email: "a@b.c" },
};

function givenProjects(projects: () => Promise<unknown>) {
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/projects")) return projects();
    if (url.startsWith("/api/experiments")) {
      return Promise.resolve({ experiments: [{ id: 2, name: "Exp B", code: null }], total: 1 });
    }
    if (url.includes("machine-types")) return Promise.resolve([]);
    if (url.includes("/environments")) return Promise.resolve({ environments: [] });
    if (url.includes("/sessions")) return Promise.resolve({ sessions: [NODE], total: 1 });
    return Promise.resolve({ repos: [] });
  });
}

const denied = () => Promise.reject(Object.assign(new Error("role_missing"), { status: 403 }));
const serverError = () => Promise.reject(Object.assign(new Error("boom"), { status: 500 }));
const networkError = () => Promise.reject(new TypeError("Failed to fetch"));

async function openForm() {
  render(<WorkNodesPage />);
  fireEvent.click(await screen.findByRole("button", { name: /new work node/i }));
  fireEvent.click(await screen.findByRole("button", { name: /^project$/i }));
}

async function recreateNode() {
  render(<WorkNodesPage />);
  fireEvent.click(await screen.findByRole("button", { name: /recreate/i }));
}

const projectPicker = () => screen.findByLabelText("Select project");

beforeEach(() => {
  mockGet.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

describe.each([
  ["opening the form", openForm],
  ["recreating a node", recreateNode],
])("%s", (_path, open) => {
  test("a 403 disables the picker, says why, and keeps the other options", async () => {
    givenProjects(denied);
    await open();

    await waitFor(async () => expect(await projectPicker()).toBeDisabled());
    expect(screen.getByText(/does not include viewing projects/i)).toBeInTheDocument();
    expect(toastMock.error).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /^experiment$/i }));
    await screen.findByRole("option", { name: "Exp B" });
  });

  test.each([
    ["a 500", serverError],
    ["a network error", networkError],
  ])("%s keeps the loading failure and the other options", async (_label, projects) => {
    givenProjects(projects);
    await open();

    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    expect(toastMock.error.mock.calls[0][0]).toMatch(/could not be loaded/i);
    expect(await projectPicker()).not.toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /^experiment$/i }));
    await screen.findByRole("option", { name: "Exp B" });
  });
});
