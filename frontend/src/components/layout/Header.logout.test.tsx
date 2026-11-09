/**
 * Logout and session expiry share one teardown. Logging out must clear the browser's
 * session even when the server's logout request fails.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: mockPush }) }));
jest.mock("@/lib/navigation", () => ({ navigateToLogin: jest.fn() }));
jest.mock("@/components/notifications/NotificationBell", () => ({ NotificationBell: () => null }));
jest.mock("@/components/infrastructure/DeploymentBanner", () => ({ DeploymentBanner: () => null }));
jest.mock("@/components/layout/GlobalSearch", () => ({ GlobalSearch: () => null }));
jest.mock("@/components/layout/QuickCreateMenu", () => ({ QuickCreateMenu: () => null }));
jest.mock("@/components/assistant/AssistantLauncher", () => ({ AssistantLauncher: () => null }));

import { Header } from "./Header";
import { getToken } from "@/lib/auth";
import { sessionSignal, startSession } from "@/lib/session";
import { permissionsCache } from "@/hooks/permissionsCache";
import { capabilitiesCache, MINIMAL_CAPABILITIES } from "@/hooks/capabilitiesCache";

// A token whose payload names the user, so the header renders the Logout button.
const TOKEN = `x.${btoa(JSON.stringify({ email: "a@lab.org" }))}.y`;
const realFetch = global.fetch;

afterEach(() => {
  global.fetch = realFetch;
  mockPush.mockReset();
});

test.each([
  ["succeeds", () => Promise.resolve({ ok: true, status: 204, json: async () => null })],
  ["fails", () => Promise.reject(new TypeError("Failed to fetch"))],
])("logout clears the session when the server request %s", async (_label, reply) => {
  startSession(TOKEN);
  permissionsCache.permissions = new Set(["users:manage"]);
  capabilitiesCache.value = MINIMAL_CAPABILITIES;
  const signal = sessionSignal();
  global.fetch = jest.fn(reply) as unknown as typeof fetch;

  render(<Header />);
  fireEvent.click(screen.getByRole("button", { name: "Logout" }));

  await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/login"));
  expect(getToken()).toBeNull();
  expect(permissionsCache.permissions).toBeNull();
  expect(capabilitiesCache.value).toBeNull();
  expect(signal.aborted).toBe(true);
});
