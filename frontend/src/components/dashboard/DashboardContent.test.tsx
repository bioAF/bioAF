import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const mockPermissions = jest.fn();
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => mockPermissions(),
}));

jest.mock("next/link", () => {
  return function MockLink({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) {
    return <a href={href}>{children}</a>;
  };
});

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), getWithRetry: jest.fn(), put: jest.fn() },
}));

import { api } from "@/lib/api";
import { DashboardContent } from "./DashboardContent";

const mockGet = api.get as jest.Mock;
const mockGetRetry = api.getWithRetry as jest.Mock;
const mockPut = api.put as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  mockGetRetry.mockReset();
  mockPut.mockReset();
  // Default both fetchers to a never-resolving promise so inner widget cards
  // mount and stay in their loading state. Each test queues the layout response
  // with mockResolvedValueOnce, which the first api.get (the layout fetch)
  // consumes before any widget calls api.get.
  mockGet.mockImplementation(() => new Promise(() => {}));
  mockGetRetry.mockImplementation(() => new Promise(() => {}));
  mockPut.mockResolvedValue({ configured: true, widgets: [] });
  mockPermissions.mockReturnValue({
    canAccess: () => true,
    roleName: "comp_bio",
    loading: false,
  });
});

test("an unconfigured comp_bio sees the comp_bio default widgets", async () => {
  mockGet.mockResolvedValueOnce({ configured: false, widgets: [] });
  render(<DashboardContent />);

  await waitFor(() => {
    expect(screen.getByTestId("widget-running-jobs")).toBeInTheDocument();
  });
  expect(screen.getByTestId("widget-queue-depth")).toBeInTheDocument();
  // cost_budget is an admin default, not a comp_bio default
  expect(screen.queryByTestId("widget-cost-budget")).not.toBeInTheDocument();
});

test("a configured user sees exactly their saved widgets", async () => {
  mockGet.mockResolvedValueOnce({ configured: true, widgets: [{ key: "cost_budget" }] });
  render(<DashboardContent />);

  await waitFor(() => {
    expect(screen.getByTestId("widget-cost-budget")).toBeInTheDocument();
  });
  expect(screen.queryByTestId("widget-running-jobs")).not.toBeInTheDocument();
});

test("a saved widget the user can no longer access is skipped, no crash", async () => {
  mockGet.mockResolvedValueOnce({ configured: true, widgets: [{ key: "cost_budget" }] });
  mockPermissions.mockReturnValue({
    canAccess: (r: string) => r !== "cost_center", // lost cost access
    roleName: "comp_bio",
    loading: false,
  });
  render(<DashboardContent />);

  await waitFor(() => {
    expect(screen.getByTestId("dashboard-empty")).toBeInTheDocument();
  });
  expect(screen.queryByTestId("widget-cost-budget")).not.toBeInTheDocument();
});

test("the gear opens the widget picker, listing accessible widgets", async () => {
  mockGet.mockResolvedValueOnce({ configured: false, widgets: [] });
  render(<DashboardContent />);

  await waitFor(() => {
    expect(screen.getByTestId("dashboard-gear")).toBeInTheDocument();
  });
  expect(screen.queryByTestId("widget-picker")).not.toBeInTheDocument();
  fireEvent.click(screen.getByTestId("dashboard-gear"));
  expect(screen.getByTestId("widget-picker")).toBeInTheDocument();
  expect(screen.getByTestId("picker-item-experiments_status")).toBeInTheDocument();
});

test("saving from the picker persists the chosen widgets via PUT", async () => {
  mockGet.mockResolvedValueOnce({ configured: false, widgets: [] });
  render(<DashboardContent />);

  await waitFor(() => expect(screen.getByTestId("dashboard-gear")).toBeInTheDocument());
  fireEvent.click(screen.getByTestId("dashboard-gear"));
  // turn experiments_status on, then save
  fireEvent.click(screen.getByTestId("picker-toggle-experiments_status"));
  fireEvent.click(screen.getByTestId("picker-save"));

  await waitFor(() => expect(mockPut).toHaveBeenCalled());
  const body = mockPut.mock.calls[0][1];
  expect(body.widgets.map((w: { key: string }) => w.key)).toContain("experiments_status");
});
