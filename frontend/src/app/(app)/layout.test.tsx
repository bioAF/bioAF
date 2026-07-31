import { render, screen } from "@testing-library/react";

const mockReplace = jest.fn();
const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace, push: mockPush }),
}));

let authed = true;
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => authed }));
jest.mock("@/components/layout/Sidebar", () => ({ Sidebar: () => <nav data-testid="app-sidebar" /> }));
jest.mock("@/components/layout/Header", () => ({ Header: () => <header data-testid="app-header" /> }));

import AppLayout from "./layout";

beforeEach(() => {
  authed = true;
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
});
