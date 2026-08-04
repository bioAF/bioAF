import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { SdrBrowser, SdrDetailView, sdrCode } from "./SdrBrowser";

let canAccessImpl = (_resource: string, _action: string) => true;

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: (r: string, a: string) => canAccessImpl(r, a) }),
}));

const pushMock = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
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
  pushMock.mockReset();
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

  fireEvent.click(screen.getByText("Use STARsolo over CellRanger"));
  expect(pushMock).toHaveBeenCalledWith("/lab-knowledge/decision-records/1");
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

test("SdrDetailView renders decision, justification, and status history", async () => {
  const detail = {
    ...makeSdr(),
    decision: "Adopt STARsolo for alignment",
    justification: "Better doublet handling and speed.",
    created_by: { id: 1, name: "Alice", email: "alice@example.com" },
    trigger_warning_sent_at: null,
    superseded_by: null,
    supersedes: null,
    transitions: [
      {
        id: 1,
        from_status: "draft",
        to_status: "active",
        note: null,
        transitioned_by: { id: 1, name: "Alice", email: "alice@example.com" },
        transitioned_at: "2026-01-15T10:00:00Z",
      },
    ],
  };
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/sdr-categories")) return Promise.resolve([]);
    if (url.includes("/sdrs/1")) return Promise.resolve(detail);
    return Promise.resolve({});
  });
  render(<SdrDetailView sdrId={1} onDeleted={jest.fn()} />);
  await waitFor(() =>
    expect(screen.getByText("Adopt STARsolo for alignment")).toBeInTheDocument(),
  );
  expect(screen.getByText("Better doublet handling and speed.")).toBeInTheDocument();
  expect(screen.getByText(/Status History/i)).toBeInTheDocument();
});

test("search box survives typing: the input stays mounted and keeps focus between keystrokes", async () => {
  render(<SdrBrowser />);
  await waitFor(() =>
    expect(screen.getByPlaceholderText("Search decision records...")).toBeInTheDocument(),
  );

  const input = screen.getByPlaceholderText("Search decision records...") as HTMLInputElement;
  input.focus();
  fireEvent.change(input, { target: { value: "c" } });

  expect(screen.queryByPlaceholderText("Search decision records...")).toBeInTheDocument();
  expect(document.activeElement).toBe(screen.getByPlaceholderText("Search decision records..."));
});
