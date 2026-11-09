/**
 * The audit-log CSV export goes through the shared application client: the configured
 * API base and session credential, the page's filters, and the backend's error text on failure.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { fakeServer } from "@/testing/fakeServer";

jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/components/layout/Breadcrumb", () => ({ Breadcrumb: () => null }));
jest.mock("@/lib/navigation", () => ({ navigateToLogin: jest.fn() }));

import AuditLogPage from "./page";
import { startSession } from "@/lib/session";
const { toastMock } = jest.requireMock("@/components/shared/Toast");

const realFetch = global.fetch;
const realEnv = process.env.NEXT_PUBLIC_API_URL;
let saved: string[];

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_URL = "https://api.lab.org";
  localStorage.clear();
  startSession("live.jwt");
  saved = [];
  (URL as unknown as { createObjectURL: unknown }).createObjectURL = jest.fn(() => "blob:x");
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = jest.fn();
  jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    saved.push(this.download);
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  global.fetch = realFetch;
  if (realEnv === undefined) delete process.env.NEXT_PUBLIC_API_URL;
  else process.env.NEXT_PUBLIC_API_URL = realEnv;
  jest.restoreAllMocks();
});

test("exports the filtered log as CSV with the session credential", async () => {
  const { requests } = fakeServer([
    ["/api/audit/export", { text: "a,b", headers: { "Content-Disposition": "attachment; filename=audit_log.csv" } }],
    ["/api/audit?", { json: { entries: [], total: 0 } }],
  ]);
  render(<AuditLogPage />);
  fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2026-01-01" } });
  fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));

  await waitFor(() => expect(saved).toEqual(["audit_log.csv"]));
  const exportRequest = requests.find((r) => r.url.includes("/api/audit/export"))!;
  expect(exportRequest.url).toBe("https://api.lab.org/api/audit/export?format=csv&start_date=2026-01-01");
  expect(exportRequest.headers.Authorization).toBe("Bearer live.jwt");
});

test("a failed export reports the backend's explanation", async () => {
  fakeServer([
    ["/api/audit/export", { status: 403, json: { detail: "You cannot export the audit log." } }],
    ["/api/audit?", { json: { entries: [], total: 0 } }],
  ]);
  render(<AuditLogPage />);
  fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));

  await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith("You cannot export the audit log."));
  expect(saved).toEqual([]);
});
