/**
 * Project linkage on a notebook launch is optional. A role that may not list
 * projects still launches, linked to an experiment.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import userEvent from "@testing-library/user-event";
import NotebooksPage from "./page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/notebooks",
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));
jest.mock("@/lib/auth", () => ({
  getToken: () => "fake-token",
  removeToken: jest.fn(),
  getCurrentUser: () => ({ role_name: "custom", email: "c@test.com" }),
  isAuthenticated: () => true,
}));
jest.mock("@/hooks/useComponents", () => ({
  useComponents: () => ({
    components: [{ key: "jupyterhub", name: "jupyterhub", description: "", category: "analysis", enabled: true, status: "ready", config: {}, dependencies: [], estimated_monthly_cost: "", updated_at: null }],
    loading: false,
    refetch: jest.fn(),
  }),
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;
const { toastMock } = jest.requireMock("@/components/shared/Toast");

function givenProjects(projects: () => Promise<unknown>) {
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/projects")) return projects();
    if (url.startsWith("/api/experiments")) {
      return Promise.resolve({ experiments: [{ id: 42, name: "Exp One", code: null }], total: 1 });
    }
    if (url.includes("sessions")) return Promise.resolve({ sessions: [] });
    if (url.includes("build-status")) return Promise.resolve({ build_id: null, build_status: null, image_uri: null });
    if (url.includes("environments")) return Promise.resolve({ environments: [], total: 0 });
    return Promise.resolve({});
  });
}

beforeEach(() => {
  mockGet.mockReset();
  toastMock.error.mockClear();
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

async function openLaunchModal(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByText("Launch Session"));
  await screen.findByLabelText("Experiment");
}

test("a 403 disables project linkage, says why, and keeps experiment linkage", async () => {
  const user = userEvent.setup();
  givenProjects(() => Promise.reject(Object.assign(new Error("role_missing"), { status: 403 })));
  render(<NotebooksPage />);
  await openLaunchModal(user);

  expect(await screen.findByRole("option", { name: "Exp One" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Project" }));

  expect(screen.getByLabelText("Project")).toBeDisabled();
  expect(screen.getByText(/does not include viewing projects/)).toBeInTheDocument();
  expect(toastMock.error).not.toHaveBeenCalled();
});

test("a failure keeps its visible report", async () => {
  givenProjects(() => Promise.reject(new Error("Backend unavailable")));
  render(<NotebooksPage />);

  await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith(expect.stringMatching(/Projects could not be loaded/)));
});
