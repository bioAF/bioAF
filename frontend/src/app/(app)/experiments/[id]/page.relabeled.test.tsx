/**
 * Samples migration 080 relabelled (plan 9): before 080 an experiment could hold one source label
 * twice, so the upgrade gave each such sample a new external ID. The label people knew stays visible
 * beside the new ID, in the table and in the sample's details.
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

const relabelled = {
  id: 5,
  external_id: "DUP~5",
  relabeled_from: "DUP",
  organism: "Mus musculus",
  tissue_type: null,
  molecule_type: null,
  status: "registered",
  qc_status: null,
  custom_fields: [],
};
const plain = { ...relabelled, id: 6, external_id: "PLAIN", relabeled_from: null };

function load() {
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/experiments/7") return Promise.resolve(experiment);
    if (url.endsWith("/samples")) return Promise.resolve([relabelled, plain]);
    if (url.includes("/audit")) return Promise.resolve({ entries: [], total: 0 });
    if (url.includes("pipeline-runs")) return Promise.resolve({ runs: [] });
    if (url.includes("notebooks/sessions")) return Promise.resolve({ sessions: [] });
    return Promise.resolve([]);
  });
}

test("the samples table shows the label a relabelled sample had before the upgrade", async () => {
  load();
  render(<ExperimentDetailPage />);
  const row = (await screen.findByText("DUP~5")).closest("tr")!;
  expect(row).toHaveTextContent("before upgrade: DUP");
  const plainRow = screen.getByText("PLAIN").closest("tr")!;
  expect(plainRow).not.toHaveTextContent("before upgrade");
});
