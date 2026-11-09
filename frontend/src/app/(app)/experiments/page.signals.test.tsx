/** The experiment list read is cancelled when its filters change and when the page closes. */
import { render, screen, fireEvent } from "@testing-library/react";
import { captureReads } from "@/testing/captureReads";

jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import ExperimentsPage from "./page";
import { api } from "@/lib/api";

test("the list read carries a signal that aborts on a filter change and on unmount", () => {
  const capture = captureReads(api.get as jest.Mock);
  const { unmount } = render(<ExperimentsPage />);
  const first = capture.latest("/api/experiments?");
  expect(first.signal).toBeInstanceOf(AbortSignal);

  fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "sequencing" } });
  expect(first.signal?.aborted).toBe(true);

  const current = capture.latest("status=sequencing");
  unmount();
  expect(current.signal?.aborted).toBe(true);
});
