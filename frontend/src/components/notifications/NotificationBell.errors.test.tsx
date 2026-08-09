/**
 * A notification count that could not be read is not a count of zero.
 *
 * Measured on the deployed demo with 241 unread: the badge read "99+" healthy
 * and rendered NOTHING once /api/notifications/unread-count failed, and opening
 * the bell said "No notifications". Both are the same defect as the four
 * dashboard widgets that reported an outage as a confident number -- a widget
 * may not decide a failed request means zero.
 *
 * The bell's button also had no accessible name at all, so a screen-reader user
 * had neither the count nor the fact that it is unknown.
 */
import { render, screen, waitFor, fireEvent } from "@/testing/renderWithProviders";
import { NotificationBell } from "./NotificationBell";

jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn() },
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;

let errorLog: jest.SpyInstance;
beforeEach(() => {
  mockGet.mockReset();
  errorLog = jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => errorLog.mockRestore());

test("a failed count is not rendered as no unread notifications", async () => {
  mockGet.mockRejectedValue(new Error("boom"));
  render(<NotificationBell />);

  await waitFor(() =>
    expect(screen.getByTestId("notification-count-unknown")).toBeInTheDocument(),
  );
  expect(errorLog).toHaveBeenCalled();
});

test("the bell says what it is, and says when the count is unknown", async () => {
  mockGet.mockResolvedValue({ count: 3 });
  const { rerender } = render(<NotificationBell />);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /3 unread/i })).toBeInTheDocument(),
  );

  mockGet.mockRejectedValue(new Error("boom"));
  rerender(<NotificationBell key="again" />);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: /unread notification count is unavailable/i }),
    ).toBeInTheDocument(),
  );
});

test("a real zero still reads as zero", async () => {
  mockGet.mockResolvedValue({ count: 0 });
  render(<NotificationBell />);
  await waitFor(() => expect(mockGet).toHaveBeenCalled());
  expect(screen.queryByTestId("notification-count-unknown")).not.toBeInTheDocument();
});

test("the dropdown reports a failed list instead of claiming there are none", async () => {
  mockGet.mockImplementation((url: string) =>
    url.includes("unread-count")
      ? Promise.resolve({ count: 0 })
      : Promise.reject(new Error("boom")),
  );
  render(<NotificationBell />);
  await waitFor(() => expect(mockGet).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: /notification/i }));

  await waitFor(() =>
    expect(screen.getByTestId("notifications-load-failed")).toBeInTheDocument(),
  );
  expect(screen.queryByText(/^No notifications$/)).not.toBeInTheDocument();
});
