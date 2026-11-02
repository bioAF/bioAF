/**
 * Each action on a project follows the grant its own route enforces: adding
 * samples needs projects:create, removing them projects:delete, and exporting
 * files:download. One role name never described all three.
 */
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import ProjectDetailPage from "./page";
import { clearPermissionsCache } from "@/hooks/usePermissions";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
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

const PROJECT = {
  id: 7,
  name: "Atlas",
  description: null,
  hypothesis: null,
  status: "active",
  owner_user_id: 1,
  owner_name: "Owner",
  sample_count: 1,
  experiment_count: 1,
  pipeline_run_count: 0,
  snapshot_count: 0,
  created_at: "2026-05-01T00:00:00Z",
  samples: [
    {
      experiment_id: 1,
      experiment_name: "Exp",
      samples: [
        {
          sample_id: 5,
          external_id: "S5",
          organism: null,
          tissue_type: null,
          qc_status: null,
          added_by: null,
          added_at: null,
          notes: null,
        },
      ],
    },
  ],
  experiments: [],
  pipeline_runs: [],
};

function givenMe(me: () => Promise<unknown>) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/auth/me") return me();
    if (url === "/api/projects/7") return Promise.resolve(PROJECT);
    return Promise.resolve({ snapshots: [], total: 0 });
  });
}

const grants = (...pairs: Array<[string, string]>) => () =>
  Promise.resolve({
    role_name: "custom",
    permissions: pairs.map(([resource, action]) => ({ resource, action })),
  });

async function renderSamplesTab() {
  render(<ProjectDetailPage />);
  fireEvent.click(await screen.findByRole("button", { name: /^samples/i }));
  await screen.findByText("S5");
}

const action = (name: RegExp) => screen.queryByRole("button", { name });
const ALL_ACTIONS = [/add samples/i, /^remove$/i, /export data/i];

beforeEach(() => {
  clearPermissionsCache();
  mockGet.mockReset();
});

test.each([
  ["projects", "create", /add samples/i],
  ["projects", "delete", /^remove$/i],
  ["files", "download", /export data/i],
])("%s:%s alone shows only its own action", async (resource, act, name) => {
  givenMe(grants([resource, act]));
  await renderSamplesTab();
  await waitFor(() => expect(action(name)).toBeInTheDocument());
  for (const other of ALL_ACTIONS) {
    if (other.source !== name.source) expect(action(other)).not.toBeInTheDocument();
  }
});

test("a role named admin with none of the grants sees no actions", async () => {
  givenMe(() => Promise.resolve({ role_name: "admin", permissions: [] }));
  await renderSamplesTab();
  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/auth/me"));
  for (const name of ALL_ACTIONS) expect(action(name)).not.toBeInTheDocument();
});

test.each<[string, () => Promise<unknown>]>([
  ["load", () => new Promise(() => {})],
  ["fail", () => Promise.reject(new Error("boom"))],
])("actions are withheld while permissions %s", async (_label, me) => {
  givenMe(me);
  await renderSamplesTab();
  for (const name of ALL_ACTIONS) expect(action(name)).not.toBeInTheDocument();
});
