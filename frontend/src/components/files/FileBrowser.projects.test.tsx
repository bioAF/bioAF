/**
 * The file browser's project filter loads apart from its experiment filter, so a
 * role that may not list projects keeps the experiments, and is told why the
 * project filter is unavailable.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import { FileBrowser } from "./FileBrowser";

jest.mock("@/lib/auth", () => ({ getCurrentUser: () => ({ role_name: "bench" }) }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true }),
}));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => true, capabilities: {}, loading: false }),
}));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), delete: jest.fn() },
  fileContentUrl: (id: number) => Promise.resolve(`/api/files/${id}/content`),
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;
const { toastMock } = jest.requireMock("@/components/shared/Toast");

function givenProjects(projects: () => Promise<unknown>) {
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/projects")) return projects();
    if (url.startsWith("/api/experiments")) {
      return Promise.resolve({ experiments: [{ id: 2, name: "Exp B" }], total: 1 });
    }
    return Promise.resolve({ files: [], total: 0, page: 1, page_size: 25, items: [] });
  });
}

beforeEach(() => {
  mockGet.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

const projectFilter = () => screen.getByLabelText("Filter by project id") as HTMLSelectElement;

test("a 403 disables the filter, says why, and still loads experiments", async () => {
  givenProjects(() => Promise.reject(Object.assign(new Error("role_missing"), { status: 403 })));
  render(<FileBrowser showProjectFilter showExperimentFilter />);

  await waitFor(() => expect(projectFilter()).toBeDisabled());
  expect(screen.getByText(/does not include viewing projects/i)).toBeInTheDocument();
  await waitFor(() =>
    expect(mockGet.mock.calls.some(([url]) => String(url).startsWith("/api/experiments"))).toBe(true),
  );
  expect(toastMock.error).not.toHaveBeenCalled();
});

test.each([
  ["a 500", () => Promise.reject(Object.assign(new Error("boom"), { status: 500 }))],
  ["a network error", () => Promise.reject(new TypeError("Failed to fetch"))],
])("%s shows a loading failure, not an empty filter", async (_label, projects) => {
  givenProjects(projects);
  render(<FileBrowser showProjectFilter />);

  await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
  expect(toastMock.error.mock.calls[0][0]).toMatch(/could not be loaded/i);
  expect(projectFilter()).not.toBeDisabled();
});

test("loaded projects are offered as before", async () => {
  givenProjects(() => Promise.resolve({ projects: [{ id: 1, name: "Atlas" }], total: 1 }));
  render(<FileBrowser showProjectFilter />);

  await screen.findByRole("option", { name: "Atlas" });
  expect(projectFilter()).not.toBeDisabled();
});
