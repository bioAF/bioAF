/**
 * The experiment list requested on every keystroke and accepted answers in arrival order, so an
 * older search could replace a newer one. Search now waits for a pause, the list is
 * keyed by query, page, status, and project, and only the current key's answer is shown.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));
// A stable router: the page reloads its project options whenever the router changes.
const routerMock = { push: jest.fn() };
jest.mock("next/navigation", () => ({ useRouter: () => routerMock }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import ExperimentsPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

function experiment(id: number, name: string) {
  return {
    id, name, status: "registered", external_id: `E-${id}`, code: null, project: null,
    owner: null, sample_count: 0, created_at: "2026-01-01T00:00:00Z",
  };
}

function take(fragment: string): Pending {
  const index = pending.findIndex((p) => p.url.includes(fragment));
  if (index < 0) throw new Error(`no pending request matching ${fragment}: ${pending.map((p) => p.url)}`);
  return pending.splice(index, 1)[0];
}

function answer(fragment: string, names: string[], total = names.length) {
  const p = take(fragment);
  return act(async () => {
    p.resolve({ experiments: names.map((n, i) => experiment(i + 1, n)), total });
  });
}

async function pause() {
  await act(async () => {
    jest.advanceTimersByTime(300);
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  pending = [];
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/projects")) return Promise.resolve({ projects: [{ id: 5, name: "Atlas" }] });
    return new Promise((resolve, reject) => pending.push({ url, resolve, reject }));
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("typing waits for a pause before searching", async () => {
  render(<ExperimentsPage />);
  await answer("/api/experiments?", ["Alpha"]);

  const input = screen.getByLabelText("Search experiments");
  fireEvent.change(input, { target: { value: "a" } });
  fireEvent.change(input, { target: { value: "al" } });
  await pause();

  const searches = mockGet.mock.calls.map((c) => c[0] as string).filter((u) => u.includes("search="));
  expect(searches).toEqual(["/api/experiments?page=1&page_size=25&search=al"]);
});

test("an older search's late answer does not replace the newer one", async () => {
  render(<ExperimentsPage />);
  await answer("/api/experiments?", ["Alpha", "Beta"]);

  fireEvent.change(screen.getByLabelText("Search experiments"), { target: { value: "a" } });
  await pause();
  fireEvent.change(screen.getByLabelText("Search experiments"), { target: { value: "be" } });
  await pause();
  await answer("search=be", ["Beta"]);
  await answer("search=a", ["Alpha", "Gamma"]);

  expect(screen.getByText("Beta")).toBeInTheDocument();
  expect(screen.queryByText("Alpha")).not.toBeInTheDocument();
  expect(screen.queryByText("Gamma")).not.toBeInTheDocument();
});

test("a new project filter returns to page 1 and does not show the previous rows as current", async () => {
  render(<ExperimentsPage />);
  await answer("/api/experiments?", ["Alpha"], 60);
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  await answer("page=2", ["Page two row"], 60);
  expect(screen.getByText("Page two row")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Filter by project"), { target: { value: "5" } });

  expect(screen.queryByText("Page two row")).not.toBeInTheDocument();
  const request = take("project_id=5");
  expect(request.url).toBe("/api/experiments?page=1&page_size=25&project_id=5");
});

test("the search box stays mounted while results load", async () => {
  render(<ExperimentsPage />);
  await answer("/api/experiments?", ["Alpha"]);
  const input = screen.getByLabelText("Search experiments");
  input.focus();
  fireEvent.change(input, { target: { value: "al" } });
  await pause();
  expect(screen.getByLabelText("Search experiments")).toBe(input);
  expect(document.activeElement).toBe(input);
});
