/**
 * The decision-record list (plan 13): debounced search already existed, but a late answer for an
 * earlier query or filter still replaced the current one, and a failed load also claimed "No
 * decision records yet".
 */
import { render, screen, fireEvent, act } from "@/testing/renderWithProviders";

jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => ({ canAccess: () => true }) }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() } }));

import { SdrBrowser } from "./SdrBrowser";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

const sdr = (id: number, title: string) => ({
  id, sdr_number: id, title, status: "active", category: null, owner: null, trigger_date: null,
  created_at: "2026-01-15T10:00:00Z", updated_at: "2026-01-15T10:00:00Z",
});

async function answer(fragment: string, titles: string[]) {
  const i = pending.findIndex((p) => p.url.includes(fragment));
  if (i < 0) throw new Error(`no pending request for ${fragment}`);
  const [p] = pending.splice(i, 1);
  await act(async () => {
    p.resolve({ sdrs: titles.map((t, n) => sdr(n + 1, t)), total: titles.length, page: 1, page_size: 50 });
  });
}

beforeEach(() => {
  pending = [];
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) =>
    url.includes("/sdr-categories")
      ? Promise.resolve([])
      : new Promise((resolve, reject) => pending.push({ url, resolve, reject })),
  );
});

test("an earlier filter's late answer does not replace the current one", async () => {
  render(<SdrBrowser />);
  fireEvent.change(screen.getByLabelText(/filter by status/i), { target: { value: "repealed" } });

  await answer("status=repealed", ["Repealed decision"]);
  await answer("/sdrs?", ["Active decision"]);

  expect(screen.getByText("Repealed decision")).toBeInTheDocument();
  expect(screen.queryByText("Active decision")).not.toBeInTheDocument();
});

test("a failed load is not reported as no decision records", async () => {
  render(<SdrBrowser />);
  const [p] = pending.splice(0, 1);
  await act(async () => {
    p.reject(new Error("Decision records are unavailable."));
  });

  expect(screen.getByText("Decision records are unavailable.")).toBeInTheDocument();
  expect(screen.queryByText(/No decision records yet/i)).not.toBeInTheDocument();
});
