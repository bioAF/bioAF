/**
 * The unread count polled with setInterval: reads could overlap, and a poll read in
 * flight when the dropdown changed the count (mark all read) could put the old count back.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

const mockGet = jest.fn();
jest.mock("@/lib/api", () => ({ api: { get: (...a: unknown[]) => mockGet(...a) } }));
jest.mock("@/components/notifications/NotificationDropdown", () => ({
  NotificationDropdown: ({ onCountChange }: { onCountChange: (n: number) => void }) => (
    <button onClick={() => onCountChange(0)}>Mark all read</button>
  ),
}));

import { NotificationBell } from "./NotificationBell";

type Pending = { resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

async function settle(fn: () => void) {
  await act(async () => {
    fn();
    await Promise.resolve();
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  pending = [];
  mockGet.mockReset();
  mockGet.mockImplementation(() => new Promise((resolve, reject) => pending.push({ resolve, reject })));
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("a slow count read is never overlapped by the next poll", async () => {
  render(<NotificationBell />);
  await act(async () => {
    jest.advanceTimersByTime(120_000);
  });
  expect(mockGet).toHaveBeenCalledTimes(1);
  await settle(() => pending[0].resolve({ count: 4 }));
  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  expect(mockGet).toHaveBeenCalledTimes(2);
});

test("marking all read is not undone by a poll read that was already in flight", async () => {
  render(<NotificationBell />);
  await settle(() => pending[0].resolve({ count: 4 }));
  await act(async () => {
    jest.advanceTimersByTime(30_000);
  });
  expect(pending).toHaveLength(2);

  fireEvent.click(screen.getByRole("button", { name: /4 unread/i }));
  fireEvent.click(screen.getByRole("button", { name: "Mark all read" }));
  await settle(() => pending[1].resolve({ count: 4 }));

  expect(screen.getByRole("button", { name: /0 unread/i })).toBeInTheDocument();
});

test("unmounting stops polling", async () => {
  const { unmount } = render(<NotificationBell />);
  await settle(() => pending[0].resolve({ count: 1 }));
  unmount();
  await act(async () => {
    jest.advanceTimersByTime(300_000);
  });
  expect(mockGet).toHaveBeenCalledTimes(1);
});
