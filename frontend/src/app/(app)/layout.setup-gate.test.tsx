/**
 * Direct navigation into the authenticated app while setup is unfinished goes to /setup (12A).
 */
import { render, waitFor } from "@testing-library/react";

const mockReplace = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ replace: mockReplace, push: jest.fn() }) }));
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

beforeEach(() => {
  mockReplace.mockReset();
  mockGet.mockReset();
});

function answer(status: unknown) {
  mockGet.mockImplementation((url: string) =>
    url === "/api/bootstrap/status" ? Promise.resolve(status) : Promise.resolve({}),
  );
}

test("an unfinished setup sends the app to /setup", async () => {
  answer({ has_admin: true, setup_complete: false });
  render(<AppLayout><main /></AppLayout>);
  await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/setup"));
});

test("a completed setup stays in the app", async () => {
  answer({ has_admin: true, setup_complete: true });
  render(<AppLayout><main /></AppLayout>);
  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/bootstrap/status"));
  expect(mockReplace).not.toHaveBeenCalled();
});

test("an unreadable status does not lock the app", async () => {
  mockGet.mockImplementation((url: string) =>
    url === "/api/bootstrap/status" ? Promise.reject(new Error("down")) : Promise.resolve({}),
  );
  render(<AppLayout><main /></AppLayout>);
  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/bootstrap/status"));
  expect(mockReplace).not.toHaveBeenCalled();
});
