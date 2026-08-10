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
// Mutable so a single suite can land on different tabs. Read at call time, not at
// mock-definition time.
let mockTabParam = "tab=samples";
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useParams: () => ({ id: "7" }),
  // The tab loads are what this is about, so land on the samples tab by default.
  useSearchParams: () => new URLSearchParams(mockTabParam),
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

/**
 * The page's OWN load, as distinct from the tab loads above. `loadExperiment` ended in
 * `catch { // handled }`, which left `experiment` null, and a null experiment renders
 * "Experiment not found". A 500 or a dropped connection was therefore reported to the
 * scientist as a deleted record: the same screen they would see for an experiment
 * somebody had genuinely removed.
 */
describe("the page's own load", () => {
  test("a 500 does not claim the experiment was deleted", async () => {
    jest.spyOn(console, "error").mockImplementation(() => {});
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/experiments/7")
        return Promise.reject(Object.assign(new Error("Server Error"), { status: 500 }));
      return Promise.resolve({});
    });

    render(<ExperimentDetailPage />);

    expect(await screen.findByTestId("experiment-load-failed")).toHaveTextContent(
      /could not be loaded/i
    );
    expect(screen.queryByText(/experiment not found/i)).not.toBeInTheDocument();
  });

  test("a 404 still says not found, because there the record really is gone", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/experiments/7")
        return Promise.reject(Object.assign(new Error("Not Found"), { status: 404 }));
      return Promise.resolve({});
    });

    render(<ExperimentDetailPage />);

    expect(await screen.findByText(/experiment not found/i)).toBeInTheDocument();
    expect(screen.queryByTestId("experiment-load-failed")).not.toBeInTheDocument();
  });
});

/**
 * The Results tab fetched QC dashboards, cellxgene publications and plots in one
 * `Promise.all` with `catch { // ignore }`. A single rejection left all three arrays
 * empty, so ONE failure produced THREE false claims at once: "No QC dashboards for this
 * experiment", "No published datasets for this experiment", "No plots for this
 * experiment". allSettled keeps the sections independent.
 */
describe("the Results tab", () => {
  beforeEach(() => {
    mockTabParam = "tab=results";
  });
  afterEach(() => {
    mockTabParam = "tab=samples";
  });

  function mountResults(failing: string) {
    jest.spyOn(console, "error").mockImplementation(() => {});
    mockGet.mockImplementation((url: string) => {
      if (url === "/api/experiments/7") return Promise.resolve(experiment);
      if (url.includes(failing)) return Promise.reject(new Error("Backend unavailable"));
      if (url.includes("/api/qc-dashboards")) return Promise.resolve([]);
      if (url.includes("/api/cellxgene")) return Promise.resolve([]);
      if (url.includes("/api/plots")) return Promise.resolve({ plots: [], total: 0 });
      return Promise.resolve({});
    });
  }

  test("one failed call does not empty the other two sections", async () => {
    mountResults("/api/plots");

    render(<ExperimentDetailPage />);

    // The plots section reports its own failure...
    expect(await screen.findByTestId("plots-section-failed")).toBeInTheDocument();
    // ...and the two that succeeded still say what they actually found.
    expect(screen.getByText(/no qc dashboards for this experiment/i)).toBeInTheDocument();
    expect(screen.getByText(/no published datasets for this experiment/i)).toBeInTheDocument();
    expect(screen.queryByText(/no plots for this experiment/i)).not.toBeInTheDocument();
  });
});
