/**
 * The experiment detail page loads each tab's contents separately, and every one
 * of those loads used to end in `catch {}`. A failed samples load rendered as an
 * experiment with no samples, which on this page is a claim about the science,
 * not about the network.
 *
 * These are tab loads, not the page's own load, so the page stays usable and the
 * failure is announced through the toast layer rather than replacing the page.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import ExperimentDetailPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));

const routerMock = { push: jest.fn(), back: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useParams: () => ({ id: "7" }),
  // The tab loads are what this is about, so land on the samples tab.
  useSearchParams: () => new URLSearchParams("tab=samples"),
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

const experiment = {
  id: 7,
  name: "GSE129538 reproduction",
  status: "processing",
  description: null,
  project: null,
  created_at: "2026-06-01T00:00:00Z",
  custom_fields: [],
  field_defaults: [],
  assay_type: null,
  organism: null,
  template_name: null,
  owner: null,
};

beforeEach(() => mockGet.mockReset());

test("a failed samples load is announced, not rendered as zero samples", async () => {
  const { toastMock } = jest.requireMock("@/components/shared/Toast");
  const errorLog = jest.spyOn(console, "error").mockImplementation(() => {});
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/experiments/7") return Promise.resolve(experiment);
    if (url.endsWith("/samples")) return Promise.reject(new Error("Backend unavailable"));
    return Promise.resolve({});
  });

  render(<ExperimentDetailPage />);

  await waitFor(() =>
    expect(toastMock.error).toHaveBeenCalledWith(
      expect.stringMatching(/samples could not be loaded/i),
    ),
  );
  // The user reads the plain sentence; the real error goes to the logs.
  expect(toastMock.error).not.toHaveBeenCalledWith(
    expect.stringContaining("Backend unavailable"),
  );
  expect(errorLog).toHaveBeenCalledWith(expect.any(String), expect.any(Error));
  errorLog.mockRestore();
});

test("a working page raises no error toast", async () => {
  const { toastMock } = jest.requireMock("@/components/shared/Toast");
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/experiments/7") return Promise.resolve(experiment);
    if (url.endsWith("/samples")) return Promise.resolve([]);
    if (url.includes("/audit")) return Promise.resolve({ entries: [], total: 0 });
    if (url.includes("pipeline-runs")) return Promise.resolve({ runs: [] });
    if (url.includes("notebooks/sessions")) return Promise.resolve({ sessions: [] });
    return Promise.resolve([]);
  });

  render(<ExperimentDetailPage />);
  await waitFor(() => expect(screen.getByText(/GSE129538 reproduction/)).toBeInTheDocument());
  expect(toastMock.error).not.toHaveBeenCalled();
});
