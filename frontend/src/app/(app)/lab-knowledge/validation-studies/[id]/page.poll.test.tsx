/**
 * A validation study advancing on its own is polled (plan 13): reads never overlap, polling stops
 * when the study leaves the advancing states, and a failed background read keeps the study on
 * screen, labelled as possibly out of date.
 */
import { render, screen, act } from "@testing-library/react";

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

type Pending = { resolve: (v: unknown) => void; reject: (e: unknown) => void };
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
  mockGet.mockImplementation(() => new Promise((resolve, reject) => reads.push({ resolve, reject })));
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("an advancing study is polled one read at a time until it stops advancing", async () => {
  render(<ValidationStudyPage />);
  await settle(() => reads[0].resolve({ id: 5, state: "running", confidence: null }));

  await advance(5000);
  expect(mockGet).toHaveBeenCalledTimes(2);
  await advance(60_000);
  expect(mockGet).toHaveBeenCalledTimes(2);

  await settle(() => reads[1].resolve({ id: 5, state: "classified", classification: "validated", confidence: 100 }));
  expect(screen.getByText("Fully Validated")).toBeInTheDocument();
  await advance(60_000);
  expect(mockGet).toHaveBeenCalledTimes(2);
});

test("a failed background read keeps the study and says it may be out of date", async () => {
  render(<ValidationStudyPage />);
  await settle(() => reads[0].resolve({ id: 5, state: "running", confidence: null }));
  await advance(5000);
  await settle(() => reads[1].reject(new Error("502")));

  expect(screen.getByText("Running analysis")).toBeInTheDocument();
  expect(screen.getByTestId("stale-notice")).toBeInTheDocument();
});
