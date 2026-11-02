/**
 * The Library's project filter is optional: a role that may not list projects
 * still reads the Library and still filters by experiment.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import LiteraturePage from "./page";
import { literature } from "@/lib/literature";
import { api } from "@/lib/api";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/lab-knowledge/literature",
}));
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ role_name: "custom" }),
}));
jest.mock("@/components/literature/AssociatePaperModal", () => ({ AssociatePaperModal: () => null }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));
jest.mock("@/lib/literature", () => {
  const actual = jest.requireActual("@/lib/literature");
  return { ...actual, literature: { listPapers: jest.fn(), bulkDismiss: jest.fn() } };
});

const mockGet = api.get as jest.Mock;
const { toastMock } = jest.requireMock("@/components/shared/Toast");

function givenProjects(projects: () => Promise<unknown>) {
  mockGet.mockImplementation((url: string) =>
    url.startsWith("/api/projects") ? projects() : Promise.resolve({ experiments: [{ id: 9, name: "Exp One" }] }),
  );
}

beforeEach(() => {
  mockGet.mockReset();
  toastMock.error.mockClear();
  (literature.listPapers as jest.Mock).mockResolvedValue({ items: [], total: 0 });
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

test("a 403 disables the project filter, says why, and keeps the experiment filter", async () => {
  givenProjects(() => Promise.reject(Object.assign(new Error("role_missing"), { status: 403 })));
  render(<LiteraturePage />);

  await waitFor(() => expect(screen.getByLabelText("Project")).toBeDisabled());
  expect(screen.getByText(/does not include viewing projects/)).toBeInTheDocument();
  expect(await screen.findByRole("option", { name: "Exp One" })).toBeInTheDocument();
  expect(screen.getByLabelText("Experiment")).toBeEnabled();
  expect(toastMock.error).not.toHaveBeenCalled();
});

test("a failure keeps its visible report", async () => {
  givenProjects(() => Promise.reject(new Error("Backend unavailable")));
  render(<LiteraturePage />);

  await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith(expect.stringMatching(/The project filter could not be loaded/)));
  expect(screen.queryByText(/does not include viewing projects/)).not.toBeInTheDocument();
});
