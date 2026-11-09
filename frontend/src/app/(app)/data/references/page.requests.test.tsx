/**
 * The reference list searched on every keystroke and accepted answers in arrival order.
 * Name search now waits for a pause, the list is keyed by query, category, scope, and status, and
 * only the current key's answer is shown.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));
const routerMock = { push: jest.fn() };
jest.mock("next/navigation", () => ({ useRouter: () => routerMock }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import ReferencesPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

function reference(id: number, name: string) {
  return {
    id, name, category: "genome", scope: "public", version: "1", status: "active",
    file_count: 1, total_size_bytes: 10, created_at: "2026-01-01T00:00:00Z",
  };
}

function take(fragment: string): Pending {
  const index = pending.findIndex((p) => p.url.includes(fragment));
  if (index < 0) throw new Error(`no pending request matching ${fragment}: ${pending.map((p) => p.url)}`);
  return pending.splice(index, 1)[0];
}

function answer(fragment: string, names: string[]) {
  const p = take(fragment);
  return act(async () => {
    p.resolve({ references: names.map((n, i) => reference(i + 1, n)), total: names.length });
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
    if (url.includes("/filter-options")) {
      return Promise.resolve({ categories: ["genome", "atlas"], scopes: ["public", "internal"] });
    }
    return new Promise((resolve, reject) => pending.push({ url, resolve, reject }));
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("name search waits for a pause", async () => {
  render(<ReferencesPage />);
  await answer("/api/references", ["GRCh38"]);

  const input = screen.getByLabelText("Search by name");
  fireEvent.change(input, { target: { value: "G" } });
  fireEvent.change(input, { target: { value: "GR" } });
  await pause();

  const searches = mockGet.mock.calls.map((c) => c[0] as string).filter((u) => u.includes("name_search="));
  expect(searches).toEqual(["/api/references?name_search=GR"]);
});

test("an older search's late answer does not replace the newer one", async () => {
  render(<ReferencesPage />);
  await answer("/api/references", ["GRCh38", "GRCm39"]);

  fireEvent.change(screen.getByLabelText("Search by name"), { target: { value: "h" } });
  await pause();
  fireEvent.change(screen.getByLabelText("Search by name"), { target: { value: "m39" } });
  await pause();
  await answer("name_search=m39", ["GRCm39"]);
  await answer("name_search=h", ["GRCh38", "hg19"]);

  expect(screen.getByText("GRCm39")).toBeInTheDocument();
  expect(screen.queryByText("hg19")).not.toBeInTheDocument();
});

test("a new scope does not present the previous scope's rows as current", async () => {
  render(<ReferencesPage />);
  await answer("/api/references", ["GRCh38"]);

  fireEvent.change(screen.getByLabelText("Filter by scope"), { target: { value: "internal" } });

  expect(screen.queryByText("GRCh38")).not.toBeInTheDocument();
  await answer("scope=internal", ["Lab atlas"]);
  expect(screen.getByText("Lab atlas")).toBeInTheDocument();
});

test("an initial failure is an error, not an empty list", async () => {
  render(<ReferencesPage />);
  await fail("/api/references");
  expect(screen.getByTestId("error-message")).toHaveTextContent(/could not be loaded/i);
  expect(screen.queryByText(/no reference datasets found/i)).not.toBeInTheDocument();
});
