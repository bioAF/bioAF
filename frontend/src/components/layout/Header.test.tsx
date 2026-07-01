import { render, screen } from "@testing-library/react";

jest.mock("@/lib/auth", () => ({
  getCurrentUser: jest.fn(),
  removeToken: jest.fn(),
}));
jest.mock("@/hooks/usePermissions", () => ({ clearPermissionsCache: jest.fn() }));
jest.mock("@/hooks/useCapabilities", () => ({ clearCapabilitiesCache: jest.fn() }));
jest.mock("@/lib/api", () => ({ api: { post: jest.fn() } }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/components/notifications/NotificationBell", () => ({
  NotificationBell: () => <div data-testid="bell" />,
}));
jest.mock("@/components/infrastructure/DeploymentBanner", () => ({
  DeploymentBanner: () => null,
}));
jest.mock("@/components/layout/GlobalSearch", () => ({
  GlobalSearch: () => <div data-testid="global-search" />,
}));
jest.mock("@/components/layout/QuickCreateMenu", () => ({
  QuickCreateMenu: () => <div data-testid="quick-create" />,
}));
jest.mock("@/components/assistant/AssistantLauncher", () => ({
  AssistantLauncher: () => <div data-testid="assistant-launcher" />,
}));
jest.mock("next/link", () => {
  return function MockLink({ children, href }: { children: React.ReactNode; href: string }) {
    return <a href={href}>{children}</a>;
  };
});

import { Header } from "./Header";
import { getCurrentUser } from "@/lib/auth";

const mockUser = getCurrentUser as jest.Mock;

test("shows global search, quick-create, and the assistant launcher when a user is logged in", () => {
  mockUser.mockReturnValue({ email: "priya@lab.org", role_name: "comp_bio" });
  render(<Header />);
  expect(screen.getByTestId("global-search")).toBeInTheDocument();
  expect(screen.getByTestId("quick-create")).toBeInTheDocument();
  expect(screen.getByTestId("assistant-launcher")).toBeInTheDocument();
});

test("hides global search and quick-create when logged out", () => {
  mockUser.mockReturnValue(null);
  render(<Header />);
  expect(screen.queryByTestId("global-search")).not.toBeInTheDocument();
  expect(screen.queryByTestId("quick-create")).not.toBeInTheDocument();
});

test("links the name to the profile page, preferring name over email", () => {
  mockUser.mockReturnValue({ email: "priya@lab.org", name: "Priya", role_name: "comp_bio" });
  render(<Header />);
  const link = screen.getByRole("link", { name: "Priya" });
  expect(link).toHaveAttribute("href", "/profile");
});

test("falls back to email in the header link when no name is set", () => {
  mockUser.mockReturnValue({ email: "priya@lab.org", role_name: "comp_bio" });
  render(<Header />);
  const link = screen.getByRole("link", { name: "priya@lab.org" });
  expect(link).toHaveAttribute("href", "/profile");
});

test("no longer shows the role badge", () => {
  mockUser.mockReturnValue({ email: "priya@lab.org", name: "Priya", role_name: "comp_bio" });
  render(<Header />);
  expect(screen.queryByText("comp_bio")).not.toBeInTheDocument();
});
