import { render, screen } from "@testing-library/react";

jest.mock("@/lib/auth", () => ({
  getCurrentUser: jest.fn(),
  removeToken: jest.fn(),
}));
jest.mock("@/hooks/usePermissions", () => ({ clearPermissionsCache: jest.fn() }));
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

import { Header } from "./Header";
import { getCurrentUser } from "@/lib/auth";

const mockUser = getCurrentUser as jest.Mock;

test("shows global search and quick-create when a user is logged in", () => {
  mockUser.mockReturnValue({ email: "priya@lab.org", role_name: "comp_bio" });
  render(<Header />);
  expect(screen.getByTestId("global-search")).toBeInTheDocument();
  expect(screen.getByTestId("quick-create")).toBeInTheDocument();
});

test("hides global search and quick-create when logged out", () => {
  mockUser.mockReturnValue(null);
  render(<Header />);
  expect(screen.queryByTestId("global-search")).not.toBeInTheDocument();
  expect(screen.queryByTestId("quick-create")).not.toBeInTheDocument();
});
