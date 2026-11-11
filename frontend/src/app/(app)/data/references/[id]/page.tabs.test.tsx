import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DataReferenceDetailPage from "./page";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  useParams: () => ({ id: "1" }),
  // The page has no ?tab= deep link; a stray one must not change the first tab.
  useSearchParams: () => new URLSearchParams("tab=details"),
}));


const mockPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ role_name: "comp_bio", sub: "1", org_id: "1" }),
}));

// The page's controls follow grants, so the fixture holds what comp_bio holds.
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({
    canAccess: (resource: string, action: string) =>
      ["references:upload", "pipelines:create"].includes(`${resource}:${action}`),
    loading: false,
    failed: false,
  }),
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

const REF_DETAIL = {
  id: 1,
  organization_id: 1,
  name: "GRCh38 GENCODE",
  category: "genome",
  scope: "public",
  version: "v45",
  source_url: null,
  gcs_prefix: "genome/grch38-gencode/v45/",
  total_size_bytes: 100,
  file_count: 1,
  status: "active",
  deprecation_note: null,
  superseded_by_id: null,
  created_at: "2026-05-01T00:00:00Z",
  files: [],
  uploaded_by: { id: 1, name: "alice", email: "alice@test.com" },
  approved_by: null,
};

beforeEach(() => {
  mockPush.mockReset();
  mockGet.mockReset();
  mockPost.mockReset();
});

/**
 * The reference detail tab strip: always opens on Files (no ?tab= deep link,
 * unlike experiments), is a named tablist, and is keyboard operable.
 */
beforeEach(() => {
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/references/1/impact")) return Promise.resolve({ pipeline_runs: [], total: 0 });
    if (url.startsWith("/api/references/1")) return Promise.resolve(REF_DETAIL);
    return Promise.resolve({ references: [], total: 0 });
  });
});

test("opens on Files whatever the URL says", async () => {
  render(<DataReferenceDetailPage />);
  const list = await screen.findByRole("tablist", { name: "Reference sections" });
  expect(within(list).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Files (0)", "Versions", "Impact", "Details"]);
  expect(screen.getByRole("tab", { selected: true })).toHaveTextContent("Files (0)");
  expect(screen.getByRole("tabpanel", { name: "Files (0)" })).toBeInTheDocument();
});

test("arrow keys move between tabs and Enter selects", async () => {
  render(<DataReferenceDetailPage />);
  await screen.findByRole("tablist", { name: "Reference sections" });
  screen.getByRole("tab", { name: "Files (0)" }).focus();
  await userEvent.keyboard("{End}");
  expect(screen.getByRole("tab", { name: "Details" })).toHaveFocus();
  await userEvent.keyboard("{Enter}");
  expect(screen.getByRole("tab", { name: "Details" })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByRole("tabpanel", { name: "Details" })).toHaveTextContent("Reference Details");
  await userEvent.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "Files (0)" })).toHaveFocus();
  await waitFor(() => expect(screen.getByRole("tab", { name: "Details" })).toHaveAttribute("tabindex", "0"));
});
