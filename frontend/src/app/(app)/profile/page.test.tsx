import { render, screen, fireEvent } from "@testing-library/react";

const mockCanAccess = jest.fn();
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: mockCanAccess, loading: false }),
}));

jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));

jest.mock("./components/AccountTab", () => ({ AccountTab: () => <div>ACCOUNT_TAB</div> }));
jest.mock("./components/SessionCredentialsTab", () => ({
  SessionCredentialsTab: () => <div>SESSION_TAB</div>,
}));
jest.mock("./components/SSHKeyTab", () => ({ SSHKeyTab: () => <div>SSH_TAB</div> }));
jest.mock("./components/NotificationsTab", () => ({ NotificationsTab: () => <div>NOTIF_TAB</div> }));

import ProfilePage from "./page";

beforeEach(() => {
  mockCanAccess.mockReset();
  mockCanAccess.mockReturnValue(true);
  window.history.replaceState(null, "", "/profile");
});

test("shows the Account tab by default", () => {
  render(<ProfilePage />);
  expect(screen.getByText("ACCOUNT_TAB")).toBeInTheDocument();
  expect(screen.queryByText("SESSION_TAB")).not.toBeInTheDocument();
});

test("switches to the Session Credentials and Git SSH Key tabs on click", () => {
  render(<ProfilePage />);
  fireEvent.click(screen.getByRole("button", { name: "Session Credentials" }));
  expect(screen.getByText("SESSION_TAB")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Git SSH Key" }));
  expect(screen.getByText("SSH_TAB")).toBeInTheDocument();
});

test("shows the Notifications tab when permitted", () => {
  mockCanAccess.mockReturnValue(true);
  render(<ProfilePage />);
  fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
  expect(screen.getByText("NOTIF_TAB")).toBeInTheDocument();
});

test("every user has the Notifications tab: their own preferences need no extra permission", () => {
  // Anyone can be sent a notification (a failed file check goes to its uploader), so anyone can
  // choose how they receive them. Organization-wide notification settings stay with admins.
  mockCanAccess.mockImplementation((resource: string) => resource !== "notifications");
  render(<ProfilePage />);
  fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
  expect(screen.getByText("NOTIF_TAB")).toBeInTheDocument();
});
