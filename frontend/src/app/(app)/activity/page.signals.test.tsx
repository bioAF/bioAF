/** The activity feed read is cancelled when its filters change and when the page closes. */
import { render, screen, fireEvent } from "@testing-library/react";
import { captureReads } from "@/testing/captureReads";

jest.mock("next/navigation", () => ({ usePathname: () => "/activity", useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/components/layout/Breadcrumb", () => ({ Breadcrumb: () => null }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import ActivityFeedPage from "./page";
import { api } from "@/lib/api";

test("the feed read carries a signal that aborts on a filter change and on unmount", () => {
  const capture = captureReads(api.get as jest.Mock);
  const { unmount } = render(<ActivityFeedPage />);
  const first = capture.latest("/api/activity-feed?");
  expect(first.signal).toBeInstanceOf(AbortSignal);

  fireEvent.change(screen.getByTestId("filter-event-type"), { target: { value: "pipeline.completed" } });
  expect(first.signal?.aborted).toBe(true);

  const current = capture.latest("pipeline.completed");
  unmount();
  expect(current.signal?.aborted).toBe(true);
});
