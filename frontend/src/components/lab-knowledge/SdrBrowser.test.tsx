import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { SdrBrowser, sdrCode } from "./SdrBrowser";

let canAccessImpl = (_resource: string, _action: string) => true;

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: (r: string, a: string) => canAccessImpl(r, a) }),
}));

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

const makeSdr = (overrides = {}) => ({
  id: 1,
  sdr_number: 1,
  title: "Use STARsolo over CellRanger",
  status: "active",
  category: { id: 2, name: "Analysis" },
  owner: { id: 1, name: "Alice", email: "alice@example.com" },
  trigger_date: null,
  created_at: "2026-01-15T10:00:00Z",
  updated_at: "2026-01-15T10:00:00Z",
  ...overrides,
});

beforeEach(() => {
  canAccessImpl = () => true;
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/sdr-categories")) return Promise.resolve([{ id: 2, name: "Analysis" }]);
    return Promise.resolve({ sdrs: [], total: 0, page: 1, page_size: 50 });
  });
});

test("sdrCode zero-pads to three digits", () => {
  expect(sdrCode(1)).toBe("SDR-001");
  expect(sdrCode(17)).toBe("SDR-017");
  expect(sdrCode(123)).toBe("SDR-123");
});

test("shows loading state initially", () => {
  mockGet.mockImplementation(() => new Promise(() => {}));
  render(<SdrBrowser />);
  expect(screen.getByTestId("sdr-loading")).toBeInTheDocument();
});

test("renders empty state when no records", async () => {
  render(<SdrBrowser />);
  await waitFor(() => {
    expect(screen.getByText(/No decision records yet/i)).toBeInTheDocument();
  });
});

test("renders SDR rows with code and status", async () => {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/sdr-categories")) return Promise.resolve([]);
    return Promise.resolve({ sdrs: [makeSdr()], total: 1, page: 1, page_size: 50 });
  });
  render(<SdrBrowser />);
  await waitFor(() => {
    expect(screen.getByText("Use STARsolo over CellRanger")).toBeInTheDocument();
  });
  expect(screen.getByText("SDR-001")).toBeInTheDocument();
  // "Active" also appears as a filter <option>; assert the row badge specifically.
  expect(screen.getAllByText("Active").some((el) => el.tagName === "SPAN")).toBe(true);
});

test("New SDR hidden without author permission", async () => {
  canAccessImpl = (_r, a) => a === "view";
  render(<SdrBrowser />);
  await waitFor(() => screen.getByText(/No decision records yet/i));
  expect(screen.queryByRole("button", { name: /new sdr/i })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /categories/i })).not.toBeInTheDocument();
});

test("New SDR shown with author permission", async () => {
  canAccessImpl = (_r, a) => a === "view" || a === "author";
  render(<SdrBrowser />);
  await waitFor(() => screen.getByText(/No decision records yet/i));
  expect(screen.getByRole("button", { name: /new sdr/i })).toBeInTheDocument();
  // categories management requires manage, not just author
  expect(screen.queryByRole("button", { name: /categories/i })).not.toBeInTheDocument();
});

test("status filter requests scoped records", async () => {
  render(<SdrBrowser />);
  await waitFor(() => screen.getByText(/No decision records yet/i));
  fireEvent.change(screen.getByLabelText(/filter by status/i), {
    target: { value: "flagged_for_review" },
  });
  await waitFor(() => {
    expect(mockGet.mock.calls.some(([url]) => url.includes("status=flagged_for_review"))).toBe(true);
  });
});

test("show historical toggle adds include_historical", async () => {
  render(<SdrBrowser />);
  await waitFor(() => screen.getByText(/No decision records yet/i));
  fireEvent.click(screen.getByLabelText(/show superseded\/repealed/i));
  await waitFor(() => {
    expect(mockGet.mock.calls.some(([url]) => url.includes("include_historical=true"))).toBe(true);
  });
});

test("search requests records with the query", async () => {
  render(<SdrBrowser />);
  await waitFor(() => screen.getByText(/No decision records yet/i));
  fireEvent.change(screen.getByLabelText(/search decision records/i), {
    target: { value: "starsolo" },
  });
  await waitFor(() => {
    expect(mockGet.mock.calls.some(([url]) => url.includes("q=starsolo"))).toBe(true);
  });
});
