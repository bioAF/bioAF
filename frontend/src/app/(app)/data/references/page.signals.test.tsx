/**
 * The reference list reads with the resource's signal: a new search cancels the previous
 * search's read rather than letting it finish unseen.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));
const routerMock = { push: jest.fn() };
jest.mock("next/navigation", () => ({ useRouter: () => routerMock }));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import ReferencesPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
let signals: AbortSignal[];

afterEach(() => jest.useRealTimers());

beforeEach(() => {
  jest.useFakeTimers();
  signals = [];
  mockGet.mockReset();
  mockGet.mockImplementation((_url: string, options?: { signal?: AbortSignal }) => {
    if (options?.signal) signals.push(options.signal);
    return new Promise(() => {});
  });
});

test("a new name search aborts the previous list read", async () => {
  render(<ReferencesPage />);
  expect(signals[0]).toBeInstanceOf(AbortSignal);
  fireEvent.change(screen.getByLabelText("Search by name"), { target: { value: "GRCh" } });
  await act(async () => {
    jest.advanceTimersByTime(2000);
  });
  expect(signals[0].aborted).toBe(true);
  expect(signals).toHaveLength(2);
});
