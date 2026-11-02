/**
 * Every mutation control on a reference follows the grant its route enforces,
 * and still needs the status the backend requires.
 */
import { render, screen, waitFor } from "@testing-library/react";
import DataReferenceDetailPage from "./page";
import { clearPermissionsCache } from "@/hooks/usePermissions";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ role_name: "admin", sub: "1", org_id: "1" }),
  getToken: () => "t",
}));
const routerMock = { push: jest.fn() };
const paramsMock = { id: "1" };
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useParams: () => paramsMock,
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

const reference = (status: string) => ({
  id: 1,
  organization_id: 1,
  name: "GRCh38",
  category: "genome",
  scope: "public",
  version: "v45",
  source_url: null,
  gcs_prefix: "genome/",
  total_size_bytes: 100,
  file_count: 1,
  status,
  deprecation_note: null,
  superseded_by_id: null,
  created_at: "2026-05-01T00:00:00Z",
  files: [],
  uploaded_by: null,
  approved_by: null,
});

function given(status: string, me: () => Promise<unknown>) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/auth/me") return me();
    if (url.endsWith("/import-status")) {
      return Promise.resolve({ status: "finalizing", bytes_downloaded: 0, total_bytes: 0 });
    }
    if (url === "/api/references/1") return Promise.resolve(reference(status));
    return Promise.resolve({ references: [], total: 0 });
  });
}

const grants = (...pairs: Array<[string, string]>) => () =>
  Promise.resolve({
    role_name: "custom",
    permissions: pairs.map(([resource, action]) => ({ resource, action })),
  });

const NONE = () => Promise.resolve({ role_name: "admin", permissions: [] });
const action = (name: RegExp) => screen.queryByRole("button", { name });

async function renderAt(status: string, me: () => Promise<unknown>) {
  given(status, me);
  render(<DataReferenceDetailPage />);
  await screen.findByRole("heading", { name: /GRCh38/ });
}

beforeEach(() => {
  clearPermissionsCache();
  mockGet.mockReset();
});

test.each<[string, [string, string], RegExp]>([
  ["active", ["references", "upload"], /upload new version/i],
  ["active", ["pipelines", "create"], /^deprecate$/i],
  ["pending_approval", ["infrastructure", "configure"], /approve deprecation/i],
  ["uploading", ["references", "upload"], /cancel import/i],
  ["uploading", ["references", "upload"], /finalize import/i],
  ["failed", ["references", "upload"], /^delete$/i],
])("at %s, %s shows %s", async (status, grant, name) => {
  await renderAt(status, grants(grant));
  await waitFor(() => expect(action(name)).toBeInTheDocument());
});

test.each<[string, RegExp]>([
  ["active", /upload new version/i],
  ["active", /^deprecate$/i],
  ["pending_approval", /approve deprecation/i],
  ["uploading", /cancel import/i],
  ["uploading", /finalize import/i],
  ["failed", /^delete$/i],
])("at %s, a role named admin without grants does not get %s", async (status, name) => {
  await renderAt(status, NONE);
  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/auth/me"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(action(name)).not.toBeInTheDocument();
});

test("deprecating does not imply uploading", async () => {
  await renderAt("active", grants(["pipelines", "create"]));
  await waitFor(() => expect(action(/^deprecate$/i)).toBeInTheDocument());
  expect(action(/upload new version/i)).not.toBeInTheDocument();
});

test("Deprecate still needs an active reference", async () => {
  await renderAt("deprecated", grants(["pipelines", "create"]));
  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/auth/me"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(action(/^deprecate$/i)).not.toBeInTheDocument();
});

test.each<[string, () => Promise<unknown>]>([
  ["load", () => new Promise(() => {})],
  ["fail", () => Promise.reject(new Error("boom"))],
])("controls are withheld while permissions %s", async (_label, me) => {
  await renderAt("uploading", me);
  expect(action(/cancel import/i)).not.toBeInTheDocument();
  expect(action(/finalize import/i)).not.toBeInTheDocument();
  expect(action(/upload new version/i)).not.toBeInTheDocument();
});
