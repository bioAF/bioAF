/**
 * The project list requested on every keystroke and accepted answers in arrival order, so an
 * older search could replace a newer one. Search now waits for typing to pause, only
 * the current query's answer is shown, and a failed refresh keeps the list, labelled as stale.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));

import ProjectsPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

function project(id: number, name: string) {
  return {
    id, name, status: "active", external_id: `P-${id}`, code: null, owner_name: "A",
    sample_count: 0, experiment_count: 0, pipeline_run_count: 0, created_at: "2026-01-01T00:00:00Z",
  };
}

function answer(fragment: string, names: string[]) {
  const index = pending.findIndex((p) => p.url.includes(fragment));
  if (index < 0) throw new Error(`no pending request matching ${fragment}`);
  const [p] = pending.splice(index, 1);
  return act(async () => {
    p.resolve({ projects: names.map((n, i) => project(i + 1, n)), total: names.length });
  });
}

function fail(fragment: string) {
  const index = pending.findIndex((p) => p.url.includes(fragment));
  const [p] = pending.splice(index, 1);
  return act(async () => {
    p.reject(new Error("502"));
  });
}

async function typeSearch(value: string) {
  fireEvent.change(screen.getByLabelText("Search projects"), { target: { value } });
  await act(async () => {
    jest.advanceTimersByTime(300);
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  pending = [];
  mockGet.mockReset();
  mockPost.mockReset();
  mockGet.mockImplementation(
    (url: string) => new Promise((resolve, reject) => pending.push({ url, resolve, reject })),
  );
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("typing waits for a pause before searching", async () => {
  render(<ProjectsPage />);
  await answer("/api/projects?", ["Alpha", "Beta"]);

  fireEvent.change(screen.getByLabelText("Search projects"), { target: { value: "a" } });
  fireEvent.change(screen.getByLabelText("Search projects"), { target: { value: "al" } });
  fireEvent.change(screen.getByLabelText("Search projects"), { target: { value: "alp" } });
  await act(async () => {
    jest.advanceTimersByTime(300);
  });

  const searches = mockGet.mock.calls.map((c) => c[0]).filter((u: string) => u.includes("search="));
  expect(searches).toEqual(["/api/projects?search=alp"]);
});

test("an older search's late answer does not replace the newer one", async () => {
  render(<ProjectsPage />);
  await answer("/api/projects?", ["Alpha", "Beta"]);

  await typeSearch("a");
  await typeSearch("be");
  await answer("search=be", ["Beta"]);
  await answer("search=a", ["Alpha", "Gamma"]);

  expect(screen.getByText("Beta")).toBeInTheDocument();
  expect(screen.queryByText("Alpha")).not.toBeInTheDocument();
  expect(screen.queryByText("Gamma")).not.toBeInTheDocument();
});

test("the search box stays mounted and focused while results load", async () => {
  render(<ProjectsPage />);
  await answer("/api/projects?", ["Alpha"]);
  const input = screen.getByLabelText("Search projects");
  input.focus();

  await typeSearch("al");

  expect(screen.getByLabelText("Search projects")).toBe(input);
  expect(document.activeElement).toBe(input);
});

test("a new status filter does not present the previous filter's rows as current", async () => {
  render(<ProjectsPage />);
  await answer("/api/projects?", ["Alpha"]);

  fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "archived" } });

  expect(screen.queryByText("Alpha")).not.toBeInTheDocument();
  await answer("status=archived", ["Old"]);
  expect(screen.getByText("Old")).toBeInTheDocument();
});

test("a failed refresh after creating keeps the list, says it may be out of date, and recovers", async () => {
  mockPost.mockResolvedValue({ id: 3 });
  render(<ProjectsPage />);
  await answer("/api/projects?", ["Alpha"]);

  fireEvent.click(screen.getAllByRole("button", { name: /new project/i })[0]);
  fireEvent.change(screen.getByPlaceholderText(/Integration Atlas/i), { target: { value: "Atlas" } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /^create project$/i }));
  });
  await fail("/api/projects?");

  expect(screen.getByText("Alpha")).toBeInTheDocument();
  expect(screen.getByTestId("stale-notice")).toHaveTextContent(/may be out of date/i);

  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await answer("/api/projects?", ["Alpha", "Atlas"]);
  expect(screen.getByText("Atlas")).toBeInTheDocument();
  expect(screen.queryByTestId("stale-notice")).not.toBeInTheDocument();
});
