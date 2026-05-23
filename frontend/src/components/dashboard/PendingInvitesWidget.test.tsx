import { render, screen, waitFor } from "@testing-library/react";
import { PendingInvitesWidget } from "./PendingInvitesWidget";

jest.mock("next/link", () => {
  return function MockLink({ children, href }: { children: React.ReactNode; href: string }) {
    return <a href={href}>{children}</a>;
  };
});
jest.mock("@/components/shared/LoadingSpinner", () => ({
  LoadingSpinner: () => <div data-testid="spinner" />,
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), getWithRetry: jest.fn() } }));

import { api } from "@/lib/api";
const mockGet = api.getWithRetry as jest.Mock;

beforeEach(() => mockGet.mockReset());

test("shows only invited users", async () => {
  mockGet.mockResolvedValueOnce({
    users: [
      { id: 1, email: "new@lab.org", name: null, status: "invited" },
      { id: 2, email: "active@lab.org", name: "Active", status: "active" },
    ],
  });
  render(<PendingInvitesWidget />);
  await waitFor(() => expect(screen.getByText("new@lab.org")).toBeInTheDocument());
  expect(screen.queryByText("active@lab.org")).not.toBeInTheDocument();
  expect(screen.getByText("Manage users")).toHaveAttribute("href", "/settings/users");
});

test("empty state when no invites are pending", async () => {
  mockGet.mockResolvedValueOnce({
    users: [{ id: 2, email: "active@lab.org", name: "Active", status: "active" }],
  });
  render(<PendingInvitesWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-empty")).toBeInTheDocument());
});

test("error state", async () => {
  mockGet.mockRejectedValueOnce(new Error("x"));
  render(<PendingInvitesWidget />);
  await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
});
