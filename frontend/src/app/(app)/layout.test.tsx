import { render, screen, fireEvent } from "@testing-library/react";

const mockReplace = jest.fn();
const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace, push: mockPush }),
}));

let authed = true;
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => authed }));
// The stand-ins carry the drawer wiring: the header asks for the nav, the
// sidebar reports whether it is open and can close itself.
jest.mock("@/components/layout/Sidebar", () => ({
  Sidebar: ({ mobileOpen, onMobileClose }: { mobileOpen?: boolean; onMobileClose?: () => void }) => (
    <nav data-testid="app-sidebar" data-mobile-open={mobileOpen ? "true" : "false"}>
      <button onClick={onMobileClose}>close nav</button>
    </nav>
  ),
}));
jest.mock("@/components/layout/Header", () => ({
  Header: ({ onOpenNav }: { onOpenNav?: () => void }) => (
    <header data-testid="app-header">
      <button onClick={onOpenNav}>open nav</button>
    </header>
  ),
}));

let backendReady = true;
let permissionsLoading = false;
let componentsLoading = false;
jest.mock("@/hooks/useBackendReady", () => ({ useBackendReady: () => ({ ready: backendReady }) }));
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => ({ loading: permissionsLoading }) }));
jest.mock("@/hooks/useComponents", () => ({ useComponents: () => ({ loading: componentsLoading }) }));

import AppLayout from "./layout";

beforeEach(() => {
  authed = true;
  backendReady = true;
  permissionsLoading = false;
  componentsLoading = false;
  mockReplace.mockReset();
  mockPush.mockReset();
});

describe("(app) route-group layout", () => {
  it("mounts the shared Sidebar + Header once around the page content", () => {
    render(
      <AppLayout>
        <main data-testid="page-content">hello</main>
      </AppLayout>
    );
    expect(screen.getByTestId("app-sidebar")).toBeInTheDocument();
    expect(screen.getByTestId("app-header")).toBeInTheDocument();
    expect(screen.getByTestId("page-content")).toBeInTheDocument();
  });

  it("renders the page content inside the shell (does not redirect) when authenticated", () => {
    render(
      <AppLayout>
        <main data-testid="page-content">hello</main>
      </AppLayout>
    );
    expect(screen.getByTestId("page-content")).toBeInTheDocument();
    expect(mockReplace).not.toHaveBeenCalled();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("redirects to /login when not authenticated", () => {
    authed = false;
    render(
      <AppLayout>
        <main data-testid="page-content">hello</main>
      </AppLayout>
    );
    expect(mockReplace).toHaveBeenCalledWith("/login");
  });

  it("shows the app-loading splash (not the shell) while the backend is not ready", () => {
    backendReady = false;
    render(
      <AppLayout>
        <main data-testid="page-content">hello</main>
      </AppLayout>
    );
    expect(screen.getByTestId("app-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("app-sidebar")).not.toBeInTheDocument();
    expect(screen.queryByTestId("page-content")).not.toBeInTheDocument();
  });

  it("shows the splash while permissions or components are still loading", () => {
    permissionsLoading = true;
    const { rerender } = render(
      <AppLayout>
        <main data-testid="page-content">hello</main>
      </AppLayout>
    );
    expect(screen.getByTestId("app-loading")).toBeInTheDocument();

    permissionsLoading = false;
    componentsLoading = true;
    rerender(
      <AppLayout>
        <main data-testid="page-content">hello</main>
      </AppLayout>
    );
    expect(screen.getByTestId("app-loading")).toBeInTheDocument();
  });
});

describe("the off-canvas navigation the narrow screens use", () => {
  it("starts closed", () => {
    render(
      <AppLayout>
        <main data-testid="page-content">hello</main>
      </AppLayout>
    );
    expect(screen.getByTestId("app-sidebar")).toHaveAttribute("data-mobile-open", "false");
  });

  it("opens when the header asks for it, and closes when the sidebar says so", () => {
    render(
      <AppLayout>
        <main data-testid="page-content">hello</main>
      </AppLayout>
    );

    fireEvent.click(screen.getByRole("button", { name: "open nav" }));
    expect(screen.getByTestId("app-sidebar")).toHaveAttribute("data-mobile-open", "true");

    fireEvent.click(screen.getByRole("button", { name: "close nav" }));
    expect(screen.getByTestId("app-sidebar")).toHaveAttribute("data-mobile-open", "false");
  });
});
