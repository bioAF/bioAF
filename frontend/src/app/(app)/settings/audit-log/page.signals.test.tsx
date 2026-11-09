/** The audit log read is cancelled when its filters change and when the page closes. */
import { render, screen, fireEvent } from "@testing-library/react";
import { captureReads } from "@/testing/captureReads";

jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/components/layout/Breadcrumb", () => ({ Breadcrumb: () => null }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), download: jest.fn() } }));

import AuditLogPage from "./page";
import { api } from "@/lib/api";

test("the audit log read carries a signal that aborts on a filter change and on unmount", () => {
  const capture = captureReads(api.get as jest.Mock);
  const { unmount } = render(<AuditLogPage />);
  const first = capture.latest("/api/audit");
  expect(first.signal).toBeInstanceOf(AbortSignal);

  fireEvent.change(screen.getByLabelText("Start date"), { target: { value: "2026-01-01" } });
  expect(first.signal?.aborted).toBe(true);

  const current = capture.latest("2026-01-01");
  unmount();
  expect(current.signal?.aborted).toBe(true);
});
