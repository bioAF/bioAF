/**
 * The validation study's polling is decided from the study the resource holds: an advancing
 * study keeps being read, including after a failed refresh, and the read is cancelled when the
 * page closes. A study that is not advancing is read once.
 */
import { render, act } from "@testing-library/react";

const mockRouter = { push: jest.fn(), back: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useParams: () => ({ id: "5" }),
  usePathname: () => "/lab-knowledge/validation-studies/5",
}));
const mockGet = jest.fn();
jest.mock("@/lib/api", () => ({ api: { get: (...a: unknown[]) => mockGet(...a), post: jest.fn(), download: jest.fn() } }));
jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));
jest.mock("@/hooks/usePermissions", () => ({ usePermissions: () => ({ canAccess: () => true, loading: false }) }));
jest.mock("@/hooks/useBetaFeatures", () => ({
  useBetaFeatures: () => ({ available: true, flags: { lit_validation: true }, loading: false }),
}));

import ValidationStudyPage from "./page";

type Pending = { signal?: AbortSignal; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let reads: Pending[];

async function settle(fn: () => void) {
  await act(async () => {
    fn();
    await Promise.resolve();
  });
}
async function advance(ms: number) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  reads = [];
  mockGet.mockReset();
  mockGet.mockImplementation(
    (_url: string, options?: { signal?: AbortSignal }) =>
      new Promise((resolve, reject) => reads.push({ signal: options?.signal, resolve, reject })),
  );
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("the study read carries a signal that aborts when the page closes", () => {
  const { unmount } = render(<ValidationStudyPage />);
  expect(reads[0].signal).toBeInstanceOf(AbortSignal);
  unmount();
  expect(reads[0].signal!.aborted).toBe(true);
});

test("a study that is not advancing is read once", async () => {
  render(<ValidationStudyPage />);
  await settle(() => reads[0].resolve({ id: 5, state: "classified", classification: "validated", confidence: 100 }));
  await advance(60_000);
  expect(mockGet).toHaveBeenCalledTimes(1);
});

test("a failed refresh of an advancing study keeps checking and recovers", async () => {
  render(<ValidationStudyPage />);
  await settle(() => reads[0].resolve({ id: 5, state: "running", confidence: null }));
  await advance(5000);
  await settle(() => reads[1].reject(new Error("502")));
  await advance(5000);
  expect(mockGet).toHaveBeenCalledTimes(3);

  await settle(() => reads[2].resolve({ id: 5, state: "classified", classification: "validated", confidence: 100 }));
  await advance(60_000);
  expect(mockGet).toHaveBeenCalledTimes(3);
});
