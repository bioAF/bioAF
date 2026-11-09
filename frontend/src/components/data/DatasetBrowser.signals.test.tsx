/** The dataset list read is cancelled when its search changes and when the browser closes. */
import { render, screen, fireEvent, act } from "@/testing/renderWithProviders";
import { captureReads } from "@/testing/captureReads";

jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));

import { DatasetBrowser } from "./DatasetBrowser";
import { api } from "@/lib/api";

beforeEach(() => jest.useFakeTimers());
afterEach(() => jest.useRealTimers());

test("the list read carries a signal that aborts on a new search and on unmount", async () => {
  const capture = captureReads(api.get as jest.Mock);
  const { unmount } = render(<DatasetBrowser />);
  const first = capture.latest("/api/datasets?");
  expect(first.signal).toBeInstanceOf(AbortSignal);

  fireEvent.change(screen.getByLabelText("Search datasets"), { target: { value: "alpha" } });
  await act(async () => {
    jest.advanceTimersByTime(300);
  });
  expect(first.signal?.aborted).toBe(true);

  const current = capture.latest("alpha");
  unmount();
  expect(current.signal?.aborted).toBe(true);
});
