/**
 * The Add Samples picker reads every sample in the Organization and leaves out
 * the ones already in the project.
 */
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import ProjectDetailPage from "./page";
import { clearPermissionsCache } from "@/hooks/usePermissions";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "custom" }),
  getToken: () => "t",
}));
const routerMock = { push: jest.fn() };
const paramsMock = { id: "7" };
const searchParamsMock = new URLSearchParams();
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useParams: () => paramsMock,
  usePathname: () => "/projects/7",
  useSearchParams: () => searchParamsMock,
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn(), delete: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

const inProject = { sample_id: 5, external_id: "S5", organism: null, tissue_type: null, qc_status: null, added_by: null, added_at: null, notes: null };
const PROJECT = {
  id: 7, name: "Atlas", description: null, hypothesis: null, status: "active", owner_user_id: 1, owner_name: "Owner",
  sample_count: 1, experiment_count: 1, pipeline_run_count: 0, snapshot_count: 0, created_at: "2026-05-01T00:00:00Z",
  samples: [{ experiment_id: 1, experiment_name: "Exp", samples: [inProject] }], experiments: [], pipeline_runs: [],
};
const option = (id: number, external_id: string | null, experiment_name = "Other Exp") => ({
  id, external_id, organism: "Homo sapiens", tissue_type: null, qc_status: null, experiment_id: 2, experiment_name,
});

function givenSamples(samples: (url: string) => Promise<unknown>) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/auth/me") return Promise.resolve({ role_name: "custom", permissions: [{ resource: "projects", action: "create" }] });
    if (url === "/api/projects/7") return Promise.resolve(PROJECT);
    if (url.startsWith("/api/samples")) return samples(url);
    return Promise.resolve({ snapshots: [], total: 0 });
  });
}

async function openPicker() {
  render(<ProjectDetailPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Samples" }));
  fireEvent.click(await screen.findByRole("button", { name: "Add Samples" }));
}

beforeEach(() => {
  clearPermissionsCache();
  mockGet.mockReset();
  mockPost.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

test("lists samples outside the project, and adds the chosen one", async () => {
  givenSamples(() => Promise.resolve({ samples: [option(5, "S5"), option(6, "S6")], total: 2 }));
  mockPost.mockResolvedValue({});
  await openPicker();

  const box = await screen.findByRole("checkbox", { name: "Select sample S6" });
  expect(screen.queryByRole("checkbox", { name: "Select sample S5" })).not.toBeInTheDocument();
  fireEvent.click(box);
  fireEvent.click(screen.getByRole("button", { name: "Add to Project" }));

  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/projects/7/samples", { sample_ids: [6] }));
});

test("a sample with no external ID still lists and survives a search", async () => {
  givenSamples(() => Promise.resolve({ samples: [option(6, null)], total: 1 }));
  await openPicker();

  expect(await screen.findByRole("checkbox", { name: "Select sample 6" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Search by sample ID, experiment, or organism"), { target: { value: "other" } });
  expect(screen.getByRole("checkbox", { name: "Select sample 6" })).toBeInTheDocument();
});

test("reads past the first page", async () => {
  const firstPage = Array.from({ length: 200 }, (_, i) => option(1000 + i, `P${i}`));
  givenSamples((url) =>
    Promise.resolve(url.includes("page=2") ? { samples: [option(2000, "Oldest")], total: 201 } : { samples: firstPage, total: 201 }),
  );
  await openPicker();

  expect(await screen.findByRole("checkbox", { name: "Select sample Oldest" })).toBeInTheDocument();
});

test("a role that may not view samples is told so", async () => {
  givenSamples(() => Promise.reject(Object.assign(new Error("role_missing"), { status: 403 })));
  await openPicker();

  expect(await screen.findByText(/does not include viewing samples/)).toBeInTheDocument();
  expect(screen.queryByText("No matching samples available.")).not.toBeInTheDocument();
});

test("a failure is reported, not shown as an empty list", async () => {
  givenSamples(() => Promise.reject(new Error("Backend unavailable")));
  await openPicker();

  expect(await screen.findByTestId("available-samples-load-failed")).toBeInTheDocument();
});
