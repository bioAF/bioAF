/** The project list read is cancelled when its filters change and when the page closes. */
import { render, screen, fireEvent } from "@testing-library/react";
import { captureReads } from "@/testing/captureReads";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));

import ProjectsPage from "./page";
import { api } from "@/lib/api";

test("the list read carries a signal that aborts on a filter change and on unmount", () => {
  const capture = captureReads(api.get as jest.Mock);
  const { unmount } = render(<ProjectsPage />);
  const first = capture.latest("/api/projects?");
  expect(first.signal).toBeInstanceOf(AbortSignal);

  fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "archived" } });
  expect(first.signal?.aborted).toBe(true);

  const current = capture.latest("status=archived");
  unmount();
  expect(current.signal?.aborted).toBe(true);
});
