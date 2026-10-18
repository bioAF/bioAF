/**
 * The cost widgets show nothing until the billing dashboard is set up.
 *
 * Before BigQuery billing export is configured, cost data is an adapter
 * estimate. Users read those estimates on the dashboard as real spend and
 * reported the mismatch with their invoice as a bug. The gate keeps the number
 * off screen until a real one exists.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BillingDashboardGate } from "./BillingDashboardGate";

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
const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

function renderGate() {
  return render(
    <BillingDashboardGate title="Cost vs. Budget" testId="widget-cost-budget">
      <div data-testid="cost-content">$123.45</div>
    </BillingDashboardGate>,
  );
}

test("asks for the billing export status", async () => {
  mockGet.mockResolvedValueOnce({ configured: true, dataset_id: "billing_export" });
  renderGate();
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(mockGet.mock.calls[0][0]).toBe("/api/v1/infrastructure/billing-export/status");
});

test("not set up: says to enable the billing dashboard and shows no cost", async () => {
  mockGet.mockResolvedValueOnce({ configured: false, dataset_id: "" });
  renderGate();
  await waitFor(() =>
    expect(screen.getByText("Please enable the billing dashboard")).toBeInTheDocument(),
  );
  expect(screen.getByRole("link", { name: "Please enable the billing dashboard" })).toHaveAttribute(
    "href",
    "/infrastructure/cost-center",
  );
  expect(screen.queryByTestId("cost-content")).not.toBeInTheDocument();
  expect(screen.getByTestId("widget-cost-budget")).toHaveTextContent("Cost vs. Budget");
});

test("dataset created but no data yet: shows Pending and no cost", async () => {
  mockGet.mockResolvedValueOnce({ configured: false, dataset_id: "billing_export" });
  renderGate();
  await waitFor(() => expect(screen.getByText("Pending")).toBeInTheDocument());
  expect(screen.queryByTestId("cost-content")).not.toBeInTheDocument();
  expect(screen.queryByText("Please enable the billing dashboard")).not.toBeInTheDocument();
});

test("the Pending info icon explains the 24-hour wait", async () => {
  mockGet.mockResolvedValueOnce({ configured: false, dataset_id: "billing_export" });
  renderGate();
  await userEvent.click(await screen.findByRole("button", { name: /why is this pending/i }));
  const dialog = await screen.findByRole("dialog");
  expect(dialog).toHaveTextContent(/24 hours/);
  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

// Creating the dataset does not enable export in Google Cloud, and nothing
// flips `configured` on its own: only Check Status in the Cost Center does.
// So Pending must point there, or a user waits on it forever.
test("Pending links to the Cost Center to finish setup", async () => {
  mockGet.mockResolvedValueOnce({ configured: false, dataset_id: "billing_export" });
  renderGate();
  await waitFor(() => expect(screen.getByText("Pending")).toBeInTheDocument());
  expect(screen.getByRole("link", { name: "Finish setup in Cost Center" })).toHaveAttribute(
    "href",
    "/infrastructure/cost-center",
  );
});

test("the Pending dialog says setup is not finished until Check Status confirms it", async () => {
  mockGet.mockResolvedValueOnce({ configured: false, dataset_id: "billing_export" });
  renderGate();
  await userEvent.click(await screen.findByRole("button", { name: /why is this pending/i }));
  const dialog = await screen.findByRole("dialog");
  expect(dialog).toHaveTextContent(/enabled? billing export/i);
  expect(dialog).toHaveTextContent(/Check Status/);
  expect(dialog).not.toHaveTextContent(/is set up/i);
  expect(within(dialog).getByRole("link", { name: "Open Cost Center" })).toHaveAttribute(
    "href",
    "/infrastructure/cost-center",
  );
});

test("set up: shows the cost widget", async () => {
  mockGet.mockResolvedValueOnce({ configured: true, dataset_id: "billing_export" });
  renderGate();
  await waitFor(() => expect(screen.getByTestId("cost-content")).toBeInTheDocument());
  expect(screen.queryByText("Please enable the billing dashboard")).not.toBeInTheDocument();
  expect(screen.queryByText("Pending")).not.toBeInTheDocument();
});

test("a failed status check is an error with Retry, not a request to enable", async () => {
  mockGet.mockRejectedValueOnce(new Error("injected 500"));
  renderGate();
  await waitFor(() => expect(screen.getByTestId("widget-error")).toBeInTheDocument());
  expect(screen.queryByText("Please enable the billing dashboard")).not.toBeInTheDocument();
  expect(screen.queryByTestId("cost-content")).not.toBeInTheDocument();

  mockGet.mockResolvedValueOnce({ configured: true, dataset_id: "billing_export" });
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(screen.getByTestId("cost-content")).toBeInTheDocument());
});
