/**
 * Paging through experiments: the range and page count follow the server's
 * total, Next/Previous request the neighbouring page, a filter change returns
 * to page 1, and New Experiment stays an ordinary link.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));
const routerMock = { push: jest.fn() };
jest.mock("next/navigation", () => ({ useRouter: () => routerMock }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import ExperimentsPage from "./page";
import { NOT_SET } from "@/lib/placeholders";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

function rows(count: number, from = 1) {
  return Array.from({ length: count }, (_, i) => ({
    id: from + i,
    name: `Exp ${from + i}`,
    status: "registered",
    external_id: null,
    code: null,
    project: null,
    owner: null,
    sample_count: 0,
    created_at: "2026-01-01T00:00:00Z",
  }));
}

function serve(total: number) {
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/projects")) return Promise.resolve({ projects: [], total: 0 });
    const page = Number(new URL(url, "http://x").searchParams.get("page"));
    const count = Math.max(0, Math.min(25, total - (page - 1) * 25));
    return Promise.resolve({ experiments: rows(count, (page - 1) * 25 + 1), total });
  });
}

const listCalls = () => mockGet.mock.calls.map((c) => c[0] as string).filter((u) => u.startsWith("/api/experiments"));

beforeEach(() => mockGet.mockReset());

test("pages through a long list and lands on a partial final page", async () => {
  serve(60);
  render(<ExperimentsPage />);
  expect(await screen.findByText("Showing 1–25 of 60")).toBeInTheDocument();
  expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();

  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(await screen.findByText("Showing 26–50 of 60")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(await screen.findByText("Showing 51–60 of 60")).toBeInTheDocument();
  expect(screen.getByText("Exp 60")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  expect(listCalls().some((u) => u.includes("page=3&page_size=25"))).toBe(true);
});

test("a single page shows no paging controls", async () => {
  serve(3);
  render(<ExperimentsPage />);
  expect(await screen.findByText("Exp 3")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
});

test("a filter change returns to page 1", async () => {
  serve(60);
  render(<ExperimentsPage />);
  await screen.findByText("Showing 1–25 of 60");
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("Showing 26–50 of 60");
  await userEvent.selectOptions(screen.getByLabelText("Filter by status"), "sequencing");
  await waitFor(() => expect(listCalls().at(-1)).toContain("page=1&page_size=25&status=sequencing"));
});

test("New Experiment is a link to the create page", async () => {
  serve(0);
  render(<ExperimentsPage />);
  await screen.findByText("No experiments found");
  for (const link of screen.getAllByRole("link", { name: "New Experiment" })) {
    expect(link).toHaveAttribute("href", "/experiments/new");
  }
});

test("a row with no recorded creation time shows the not-set placeholder, not 1970", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/projects")) return Promise.resolve({ projects: [], total: 0 });
    return Promise.resolve({ experiments: [{ ...rows(1)[0], created_at: null }], total: 1 });
  });
  render(<ExperimentsPage />);
  const row = (await screen.findByText("Exp 1")).closest("tr")!;
  expect(row).not.toHaveTextContent("1970");
  expect(row.lastElementChild).toHaveTextContent(NOT_SET);
});
