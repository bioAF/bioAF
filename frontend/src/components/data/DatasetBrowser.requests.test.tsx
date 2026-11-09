/**
 * The dataset list searched on every keystroke and accepted answers in arrival order.
 * Search now waits for a pause, the list is keyed by query, page, and every server-side filter,
 * and only the current key's answer is shown. Selection survives a new search.
 */
import { render, screen, fireEvent, act } from "@/testing/renderWithProviders";

jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));

import { DatasetBrowser } from "./DatasetBrowser";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

function dataset(id: number, name: string) {
  return {
    experiment_id: id, experiment_name: name, project_name: null, organism: null,
    sample_count: 1, status: "registered", molecule_type: null, instrument_model: null,
    review_status: null,
  };
}

function take(fragment: string): Pending {
  const index = pending.findIndex((p) => p.url.includes(fragment));
  if (index < 0) throw new Error(`no pending request matching ${fragment}: ${pending.map((p) => p.url)}`);
  return pending.splice(index, 1)[0];
}

function answer(fragment: string, rows: Array<[number, string]>) {
  const p = take(fragment);
  return act(async () => {
    p.resolve({ experiments: rows.map(([id, n]) => dataset(id, n)), total: rows.length });
  });
}

function fail(fragment: string) {
  const p = take(fragment);
  return act(async () => {
    p.reject(new Error("502"));
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
    if (url.startsWith("/api/datasets/filter-options")) return Promise.resolve({ organisms: ["human"] });
    return new Promise((resolve, reject) => pending.push({ url, resolve, reject }));
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("search waits for a pause", async () => {
  render(<DatasetBrowser />);
  await answer("/api/datasets?", [[1, "Alpha"]]);

  fireEvent.change(screen.getByLabelText("Search datasets"), { target: { value: "a" } });
  fireEvent.change(screen.getByLabelText("Search datasets"), { target: { value: "al" } });
  await pause();

  const searches = mockGet.mock.calls.map((c) => c[0] as string).filter((u) => u.includes("query="));
  expect(searches).toEqual(["/api/datasets?page=1&page_size=20&query=al"]);
});

test("an older search's late answer does not replace the newer one, and selection survives", async () => {
  render(<DatasetBrowser />);
  await answer("/api/datasets?", [[1, "Alpha"], [2, "Beta"]]);
  fireEvent.click(screen.getByLabelText("Select Beta"));

  fireEvent.change(screen.getByLabelText("Search datasets"), { target: { value: "a" } });
  await pause();
  fireEvent.change(screen.getByLabelText("Search datasets"), { target: { value: "be" } });
  await pause();
  await answer("query=be", [[2, "Beta"]]);
  await answer("query=a", [[1, "Alpha"], [3, "Gamma"]]);

  expect(screen.getByText("Beta")).toBeInTheDocument();
  expect(screen.queryByText("Gamma")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Select Beta")).toBeChecked();
  expect(screen.getByRole("button", { name: /Add to Project \(1\)/ })).toBeInTheDocument();
});

test("a new organism filter does not present the previous rows as current", async () => {
  render(<DatasetBrowser />);
  await answer("/api/datasets?", [[1, "Alpha"]]);

  fireEvent.change(screen.getByLabelText("Filter by organism"), { target: { value: "human" } });

  expect(screen.queryByText("Alpha")).not.toBeInTheDocument();
  await answer("organism=human", [[4, "Human set"]]);
  expect(screen.getByText("Human set")).toBeInTheDocument();
});

test("an initial failure is an error with retry, not an empty list", async () => {
  render(<DatasetBrowser />);
  await fail("/api/datasets?");
  expect(screen.getByTestId("error-message")).toHaveTextContent(/datasets could not be loaded/i);
  expect(screen.queryByText(/no datasets found/i)).not.toBeInTheDocument();

  fireEvent.click(screen.getByTestId("error-retry"));
  await answer("/api/datasets?", [[1, "Alpha"]]);
  expect(screen.getByText("Alpha")).toBeInTheDocument();
  expect(screen.queryByTestId("error-message")).not.toBeInTheDocument();
});
