/**
 * The authenticated shell asks for the installation metrics choice on app entry, a refreshed session
 * included, while the installation's choice is pending (ADR-075).
 */
import { render, screen } from "@testing-library/react";

jest.mock("next/navigation", () => ({ useRouter: () => ({ replace: jest.fn(), push: jest.fn() }) }));
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));
jest.mock("@/components/layout/Sidebar", () => ({ Sidebar: () => <nav /> }));
jest.mock("@/components/layout/Header", () => ({ Header: () => <header /> }));
jest.mock("@/hooks/useBackendReady", () => ({ useBackendReady: () => ({ ready: true }) }));
jest.mock("@/hooks/useComponents", () => ({ useComponents: () => ({ loading: false }) }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ loading: false, canAccess: () => true }),
}));
const mockGet = jest.fn();
jest.mock("@/lib/api", () => ({ api: { get: (...args: unknown[]) => mockGet(...args), put: jest.fn() } }));

import AppLayout from "./layout";

test("an admin entering the app with a pending choice is asked", async () => {
  mockGet.mockResolvedValue({
    install_metrics: {
      preference: "pending",
      prompt_required: true,
      effective: false,
      operator_override: false,
      setup_completed: true,
      installation_id: null,
      notice_version: "2026-11-15",
      decided_at: null,
      revision: 0,
    },
  });

  render(
    <AppLayout>
      <main data-testid="page">page</main>
    </AppLayout>,
  );

  expect(await screen.findByRole("dialog", { name: "Help improve bioAF" })).toBeInTheDocument();
  expect(screen.getByTestId("page")).toBeInTheDocument();
  expect(mockGet).toHaveBeenCalledWith("/api/system-settings", expect.anything());
});
