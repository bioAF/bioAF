/**
 * The run list pages through the server's answer with the shared pagination
 * shell: the rows-per-page choice is always offered, page controls appear once
 * there is a second page, and the final page is partial.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";
import userEvent from "@testing-library/user-event";
import PipelineRunsPage from "./page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));
const routerMock = { push: jest.fn(), back: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => routerMock,
  useSearchParams: () => new URLSearchParams(),
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({ has: () => false, loading: false }),
}));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  api: { get: jest.fn(), post: jest.fn() },
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

function serve(total: number) {
  mockGet.mockImplementation((url: string) => {
    const q = new URL(url, "http://x").searchParams;
    const page = Number(q.get("page"));
    const size = Number(q.get("page_size"));
    const count = Math.max(0, Math.min(size, total - (page - 1) * size));
    const runs = Array.from({ length: count }, (_, i) => ({
      id: total - ((page - 1) * size + i),
      pipeline_name: "p",
      pipeline_key: "p",
      status: "completed",
      experiment: null,
      submitted_by: null,
      progress: null,
      cost_estimate: null,
      started_at: null,
      completed_at: null,
      created_at: "2026-01-01T00:00:00Z",
    }));
    return Promise.resolve({ runs, total, page, page_size: size });
  });
}

const runsUrls = () => mockGet.mock.calls.map((c) => String(c[0])).filter((u) => u.includes("/api/pipeline-runs"));

beforeEach(() => {
  mockGet.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());

test("an empty list still offers the page size and no page controls", async () => {
  serve(0);
  render(<PipelineRunsPage />);
  expect(await screen.findByText("No pipeline runs")).toBeInTheDocument();
  expect(screen.getByLabelText("Rows per page")).toHaveValue("25");
  expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
});

test("pages forward to a partial final page", async () => {
  serve(60);
  render(<PipelineRunsPage />);
  expect(await screen.findByText("Showing 1–25 of 60")).toBeInTheDocument();
  expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await userEvent.click(await screen.findByRole("button", { name: "Next" }));
  expect(await screen.findByText("Showing 51–60 of 60")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  await waitFor(() => expect(runsUrls().at(-1)).toContain("page=3&page_size=25"));
});

test("a larger page size returns to page 1 and fits everything on it", async () => {
  serve(60);
  render(<PipelineRunsPage />);
  await screen.findByText("Showing 1–25 of 60");
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("Showing 26–50 of 60");
  await userEvent.selectOptions(screen.getByLabelText("Rows per page"), "100");
  await waitFor(() => expect(runsUrls().at(-1)).toContain("page=1&page_size=100"));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Next" })).toBeNull());
});
