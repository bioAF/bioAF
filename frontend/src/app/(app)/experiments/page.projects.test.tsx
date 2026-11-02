/**
 * The project filter is optional. A role that may not list projects still reads
 * experiments, and the filter says why it is unavailable.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import ExperimentsPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "custom" }),
}));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;
const { toastMock } = jest.requireMock("@/components/shared/Toast");

function givenProjects(projects: () => Promise<unknown>) {
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/projects")) return projects();
    return Promise.resolve({ experiments: [{ id: 1, name: "Exp One", status: "registered", sample_count: 0, created_at: "2026-01-01T00:00:00Z" }], total: 1 });
  });
}

beforeEach(() => {
  mockGet.mockReset();
  toastMock.error.mockClear();
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

const filter = () => screen.getByLabelText("Filter by project") as HTMLSelectElement;

test("a 403 disables the filter, says why, and keeps the experiments", async () => {
  givenProjects(() => Promise.reject(Object.assign(new Error("role_missing"), { status: 403 })));
  render(<ExperimentsPage />);

  await waitFor(() => expect(filter()).toBeDisabled());
  expect(screen.getByText(/does not include viewing projects/)).toBeInTheDocument();
  expect(await screen.findByText("Exp One")).toBeInTheDocument();
  expect(toastMock.error).not.toHaveBeenCalled();
});

test("a failure keeps its visible report and leaves the filter alone", async () => {
  givenProjects(() => Promise.reject(new Error("Backend unavailable")));
  render(<ExperimentsPage />);

  await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith(expect.stringMatching(/The project filter could not be loaded/)));
  expect(filter()).toBeEnabled();
  expect(screen.queryByText(/does not include viewing projects/)).not.toBeInTheDocument();
});

test("a loaded list fills the filter", async () => {
  givenProjects(() => Promise.resolve({ projects: [{ id: 2, name: "Atlas" }] }));
  render(<ExperimentsPage />);

  expect(await screen.findByRole("option", { name: "Atlas" })).toBeInTheDocument();
  expect(filter()).toBeEnabled();
});
