/**
 * A role that may not list projects can still create an experiment: the rest of
 * the form loads, and the project picker says why it is unavailable instead of
 * offering an empty list.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import NewExperimentPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "bench" }),
  getToken: () => "t",
}));
const routerMock = { push: jest.fn() };
const searchParamsMock = new URLSearchParams();
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useSearchParams: () => searchParamsMock,
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;
const { toastMock } = jest.requireMock("@/components/shared/Toast");

function givenProjects(projects: () => Promise<unknown>) {
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/projects")) return projects();
    if (url.startsWith("/api/templates")) return Promise.resolve([{ id: 3, name: "Template Z" }]);
    if (url.startsWith("/api/naming-profiles")) return Promise.resolve([{ id: 4, name: "Profile Q" }]);
    return Promise.resolve({ values: [], fields: {} });
  });
}

beforeEach(() => {
  mockGet.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

const projectPicker = () => screen.getByLabelText("Project") as HTMLSelectElement;

test("a 403 disables the picker, says why, and keeps the other options", async () => {
  givenProjects(() => Promise.reject(Object.assign(new Error("role_missing"), { status: 403 })));
  render(<NewExperimentPage />);

  await waitFor(() => expect(projectPicker()).toBeDisabled());
  expect(screen.getByText(/does not include viewing projects/i)).toBeInTheDocument();
  await screen.findByRole("option", { name: "Template Z" });
  expect(toastMock.error).not.toHaveBeenCalled();
});

test.each([
  ["a 500", () => Promise.reject(Object.assign(new Error("boom"), { status: 500 }))],
  ["a network error", () => Promise.reject(new TypeError("Failed to fetch"))],
])("%s keeps the loading failure and the other options", async (_label, projects) => {
  givenProjects(projects);
  render(<NewExperimentPage />);

  await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
  expect(toastMock.error.mock.calls[0][0]).toMatch(/could not be loaded/i);
  await screen.findByRole("option", { name: "Template Z" });
  expect(projectPicker()).not.toBeDisabled();
  expect(screen.queryByText(/does not include viewing projects/i)).not.toBeInTheDocument();
});

test("loaded projects are offered as before", async () => {
  givenProjects(() => Promise.resolve({ projects: [{ id: 1, name: "Atlas" }], total: 1 }));
  render(<NewExperimentPage />);

  await screen.findByRole("option", { name: "Atlas" });
  expect(projectPicker()).not.toBeDisabled();
});
