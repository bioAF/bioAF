/** The plot archive read is cancelled when its search changes and when the page closes. */
import { render, screen, fireEvent, act } from "@testing-library/react";
import { captureReads } from "@/testing/captureReads";

jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn() },
  fileContentUrl: async () => "",
  plotThumbnailContentUrl: async () => "",
}));
jest.mock("@/hooks/useContentUrl", () => ({
  useFileContentUrl: () => null,
  usePlotThumbnailContentUrl: () => null,
}));

import PlotArchivePage from "./page";
import { api } from "@/lib/api";

beforeEach(() => jest.useFakeTimers());
afterEach(() => jest.useRealTimers());

test("the grid read carries a signal that aborts on a new search and on unmount", async () => {
  const capture = captureReads(api.get as jest.Mock);
  const { unmount } = render(<PlotArchivePage />);
  const first = capture.latest("/api/plots?");
  expect(first.signal).toBeInstanceOf(AbortSignal);

  fireEvent.change(screen.getByLabelText("Search"), { target: { value: "heat" } });
  await act(async () => {
    jest.advanceTimersByTime(300);
  });
  expect(first.signal?.aborted).toBe(true);

  const current = capture.latest("heat");
  unmount();
  expect(current.signal?.aborted).toBe(true);
});
