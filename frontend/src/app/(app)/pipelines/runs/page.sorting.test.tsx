/**
 * Sorting a paginated list must ask the server, not reorder the page.
 *
 * Proven on the deployed demo before this changed: 29 runs, page size 25,
 * backend ordering created_at DESC. Clicking "ID" ascending put run **#5** at
 * the top. The lowest ID in the list was **#1**, on page 2. The user asked for
 * the smallest and was shown the smallest of an arbitrary subset.
 *
 * `[...runs].sort(...)` cannot be fixed in place, because page 1 does not
 * contain the answer. So the click now changes the request.
 */
import { render, screen, waitFor, fireEvent } from "@/testing/renderWithProviders";
import PipelineRunsPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));
// Hoisted, not built per call: `router` is a dependency of the load effect, so
// a fresh object each render re-fires the effect forever and the page never
// leaves its loading skeleton.
const routerMock = { push: jest.fn(), back: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useSearchParams: () => new URLSearchParams(),
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
// Left real, the capabilities hook issues its own api.get and the page sits on
// its loading skeleton, so the table never renders to be clicked.
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => false, loading: false }),
}));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  api: { get: jest.fn(), post: jest.fn() },
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

/** Every URL the page has asked for. */
const urls = () => mockGet.mock.calls.map((c) => String(c[0]));
const lastRunsUrl = () => urls().filter((u) => u.includes("/api/pipeline-runs")).at(-1) ?? "";

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockResolvedValue({ runs: [], total: 0, page: 1, page_size: 25 });
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

test("the first load asks for the default page size and no sort", async () => {
  render(<PipelineRunsPage />);
  await waitFor(() => expect(lastRunsUrl()).toContain("page_size=25"));
  expect(lastRunsUrl()).not.toContain("sort_by");
});

const tableReady = () =>
  waitFor(() => expect(screen.getByRole("columnheader", { name: /^run/i })).toBeInTheDocument());

test("clicking a column header asks the server to sort", async () => {
  render(<PipelineRunsPage />);
  await tableReady();

  fireEvent.click(screen.getByRole("columnheader", { name: /^run/i }));

  await waitFor(() => expect(lastRunsUrl()).toContain("sort_by=id"));
  expect(lastRunsUrl()).toContain("sort_dir=desc");
});

test("clicking the same header again flips the direction, at the server", async () => {
  render(<PipelineRunsPage />);
  await tableReady();

  const header = () => screen.getByRole("columnheader", { name: /^run/i });
  fireEvent.click(header());
  await waitFor(() => expect(header()).toHaveAttribute("aria-sort", "descending"));
  expect(lastRunsUrl()).toContain("sort_dir=desc");

  fireEvent.click(header());
  await waitFor(() => expect(header()).toHaveAttribute("aria-sort", "ascending"));
  expect(lastRunsUrl()).toContain("sort_dir=asc");
});

test("changing the page size asks for it, and returns to page 1", async () => {
  render(<PipelineRunsPage />);
  await tableReady();

  fireEvent.change(screen.getByRole("combobox", { name: /rows per page/i }), {
    target: { value: "100" },
  });

  await waitFor(() => expect(lastRunsUrl()).toContain("page_size=100"));
  // Page 12 of 25-row pages does not exist at 100 a page, so staying on it
  // would show an empty table for a list that is not empty.
  expect(lastRunsUrl()).toContain("page=1");
});

test("the page no longer reorders rows itself", async () => {
  // The server's order is the answer. If the client re-sorted, the row order
  // here would come back ascending regardless of what the server sent.
  mockGet.mockResolvedValue({
    runs: [
      { id: 9, pipeline_name: "zulu", pipeline_key: "zulu", status: "completed", experiment: null, submitted_by: null, progress: null, cost_estimate: null, started_at: null, completed_at: null, created_at: "2026-01-01T00:00:00Z" },
      { id: 3, pipeline_name: "alpha", pipeline_key: "alpha", status: "failed", experiment: null, submitted_by: null, progress: null, cost_estimate: null, started_at: null, completed_at: null, created_at: "2026-01-02T00:00:00Z" },
    ],
    total: 2,
    page: 1,
    page_size: 25,
  });
  render(<PipelineRunsPage />);

  await waitFor(() => expect(screen.getByText(/#9/)).toBeInTheDocument());
  const body = document.querySelector("tbody")!;
  const order = Array.from(body.querySelectorAll("tr"))
    .map((tr) => (tr.textContent || "").match(/#(\d+)/)?.[1])
    .filter(Boolean);
  expect(order).toEqual(["9", "3"]);
});
