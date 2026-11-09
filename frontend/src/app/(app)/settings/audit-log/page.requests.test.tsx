/**
 * The audit log accepted list answers in arrival order, so a slow answer for an earlier page or
 * filter could replace the current one (plan 13). The list is keyed by page, entity, action, and
 * dates, and only the current key's answer is shown.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

const routerMock = { push: jest.fn() };
jest.mock("next/navigation", () => ({ useRouter: () => routerMock }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/components/layout/Breadcrumb", () => ({ Breadcrumb: () => null }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), download: jest.fn() } }));

import AuditLogPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

function entry(id: number, description: string) {
  return {
    id, timestamp: "2026-06-30T00:00:00Z", user: { id: 1, email: "a@lab.org", name: "A" },
    entity_type: "project", entity_id: id, action: "create", details: { description },
    previous_value: null,
  };
}

/** The newest pending request for `fragment`: the one the page currently owns. */
function take(fragment: string): Pending {
  const index = pending.map((p) => p.url.includes(fragment)).lastIndexOf(true);
  if (index < 0) throw new Error(`no pending request matching ${fragment}: ${pending.map((p) => p.url)}`);
  return pending.splice(index, 1)[0];
}

function answer(fragment: string, descriptions: string[], total = descriptions.length) {
  const p = take(fragment);
  return act(async () => {
    p.resolve({ entries: descriptions.map((d, i) => entry(i + 1, d)), total });
  });
}

beforeEach(() => {
  pending = [];
  mockGet.mockReset();
  mockGet.mockImplementation(
    (url: string) => new Promise((resolve, reject) => pending.push({ url, resolve, reject })),
  );
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

test("a slow answer for the previous page does not replace the current page", async () => {
  render(<AuditLogPage />);
  await answer("page=1", ["page one entry"], 60);

  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  fireEvent.click(screen.getByRole("button", { name: "Previous" }));
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await answer("page=2", ["page two entry"], 60);
  // The superseded requests answer late, in reverse order.
  await answer("page=1", ["late page one entry"], 60);
  await answer("page=2", ["late first page two entry"], 60);

  expect(screen.getByText("page two entry")).toBeInTheDocument();
  expect(screen.queryByText("late page one entry")).not.toBeInTheDocument();
});

test("a new date filter returns to page 1 and does not show the previous entries as current", async () => {
  render(<AuditLogPage />);
  await answer("page=1", ["old entry"]);

  fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2026-01-01" } });

  expect(screen.queryByText("old entry")).not.toBeInTheDocument();
  const request = take("start_date=2026-01-01");
  expect(request.url).toBe("/api/audit?page=1&page_size=25&start_date=2026-01-01");
});
