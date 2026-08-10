/**
 * What the app shell does when one of its three boot dependencies fails.
 *
 * Measured on the deployed app 2026-08-07, and this is what these tests exist to
 * stop happening again:
 *
 *  - `/api/health/ready` 500 while everything else was healthy: the full-screen
 *    splash reading "Loading bioAF..." held forever. 15 probes in 30s at 2005ms
 *    intervals, **0 focusable elements**, 0 live regions, no message, no retry.
 *    The entire UI was the string "bioAF Loading bioAF...".
 *  - `/api/auth/me` 500: sidebar collapsed to one item, dashboard read "Your
 *    dashboard has no widgets. Add widgets". A failed load rendered as a
 *    preference.
 *  - `/api/v1/infrastructure/stack/components` 500: the Pipelines section
 *    vanished from the sidebar with no error. Covered by
 *    useVisibleNavSections.test.tsx; here we assert the shell still boots.
 *
 * The rule these encode: a boot dependency that cannot be loaded gets a plain
 * sentence, a focusable way to try again, and an announcement. Never a spinner.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockBackendReady = jest.fn();
jest.mock("@/hooks/useBackendReady", () => ({
  useBackendReady: () => mockBackendReady(),
}));

const mockPermissions = jest.fn();
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => mockPermissions(),
  clearPermissionsCache: jest.fn(),
}));

const mockComponents = jest.fn();
jest.mock("@/hooks/useComponents", () => ({
  useComponents: () => mockComponents(),
  invalidateComponentCache: jest.fn(),
}));

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ name: "Test", email: "t@b.co" }),
  removeToken: jest.fn(),
}));

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: jest.fn(), push: jest.fn() }),
  usePathname: () => "/dashboard",
}));

// The shell mounts the real Sidebar and Header; both pull in a lot of unrelated
// machinery. Stub them so these tests are about the boot gate only.
jest.mock("@/components/layout/Sidebar", () => ({
  Sidebar: () => <aside data-testid="sidebar" />,
}));
jest.mock("@/components/layout/Header", () => ({
  Header: () => <header data-testid="header" />,
}));

import AppLayout from "@/app/(app)/layout";

const READY = { ready: true, unreachable: false, retryNow: jest.fn() };
const LOADED = { loading: false, failed: false };

beforeEach(() => {
  jest.clearAllMocks();
  mockBackendReady.mockReturnValue(READY);
  mockPermissions.mockReturnValue({ ...LOADED, canAccess: () => true, roleName: "admin" });
  mockComponents.mockReturnValue({ ...LOADED, components: [] });
});

function renderShell() {
  return render(
    <AppLayout>
      <main data-testid="page">page content</main>
    </AppLayout>,
  );
}

describe("the app shell while it is still booting", () => {
  it("shows the branded splash, and announces it", () => {
    mockBackendReady.mockReturnValue({ ready: false, unreachable: false, retryNow: jest.fn() });
    renderShell();

    const splash = screen.getByTestId("app-loading");
    expect(splash).toBeInTheDocument();
    // Even the ordinary wait has to be announced, or a screen reader user is
    // told nothing at all while the app boots.
    expect(splash).toHaveAttribute("role", "status");
    expect(screen.queryByTestId("page")).not.toBeInTheDocument();
  });
});

describe("the app shell when the backend cannot be reached", () => {
  beforeEach(() => {
    mockBackendReady.mockReturnValue({ ready: false, unreachable: true, retryNow: jest.fn() });
  });

  it("says so in a plain sentence instead of holding a spinner", () => {
    renderShell();
    const splash = screen.getByTestId("app-loading");

    expect(splash).toHaveTextContent(/cannot be reached|could not be reached/i);
    expect(splash).not.toHaveTextContent(/Loading bioAF/i);
  });

  it("gives the user something focusable to act on", () => {
    renderShell();
    const retry = screen.getByRole("button", { name: /try again/i });
    expect(retry).toBeInTheDocument();
    expect(retry).toBeEnabled();
  });

  it("re-checks when the user asks it to", async () => {
    const retryNow = jest.fn();
    mockBackendReady.mockReturnValue({ ready: false, unreachable: true, retryNow });
    renderShell();

    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(retryNow).toHaveBeenCalled();
  });

  it("keeps the message in a live region so it is announced", () => {
    renderShell();
    expect(screen.getByTestId("app-loading")).toHaveAttribute("role", "status");
  });

  it("does not leak a technical detail onto the screen", () => {
    renderShell();
    const text = screen.getByTestId("app-loading").textContent ?? "";
    expect(text).not.toMatch(/500|fetch|TypeError|undefined|\/api\//);
  });
});

describe("the app shell when permissions cannot be loaded", () => {
  beforeEach(() => {
    mockPermissions.mockReturnValue({
      loading: false,
      failed: true,
      canAccess: () => false,
      roleName: "",
    });
  });

  /**
   * The worst outcome here is the one that shipped: a fully navigable app in
   * which the user can do nothing, which reads as "my account is empty" rather
   * than "we could not load your account".
   */
  it("says the account could not be loaded rather than rendering an empty account", () => {
    renderShell();

    expect(screen.getByTestId("app-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("page")).not.toBeInTheDocument();
    expect(screen.getByTestId("app-loading")).toHaveTextContent(
      /permissions could not be loaded|account could not be loaded/i,
    );
  });

  it("offers a way to try again", () => {
    renderShell();
    expect(screen.getByRole("button", { name: /try again/i })).toBeEnabled();
  });
});

describe("the app shell when the installed-component check failed", () => {
  /**
   * Unlike the other two, this one must NOT block the app. The component list
   * only decides which optional sections appear; failing to read it is not a
   * reason to withhold the whole product. useVisibleNavSections keeps the gated
   * sections visible instead (covered in its own test).
   */
  it("still renders the app", () => {
    mockComponents.mockReturnValue({ loading: false, failed: true, components: [] });
    renderShell();

    expect(screen.getByTestId("page")).toBeInTheDocument();
    expect(screen.queryByTestId("app-loading")).not.toBeInTheDocument();
  });
});

describe("the app shell once everything is loaded", () => {
  it("renders the page inside the shell, with no splash", async () => {
    renderShell();
    await waitFor(() => expect(screen.getByTestId("page")).toBeInTheDocument());
    expect(screen.queryByTestId("app-loading")).not.toBeInTheDocument();
    expect(screen.getByTestId("sidebar")).toBeInTheDocument();
  });
});
