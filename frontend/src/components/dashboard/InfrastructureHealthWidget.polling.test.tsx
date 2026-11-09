/**
 * Service health polls every 60 s on the shared polling primitive (plan 13): reads do not
 * overlap, a failed background refresh keeps the last health visible and says it is stale, and
 * Retry recovers this widget alone.
 */
import { render, screen, act, fireEvent } from "@testing-library/react";

jest.mock("@/components/shared/LoadingSpinner", () => ({ LoadingSpinner: () => null }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import { api } from "@/lib/api";
import { InfrastructureHealthWidget } from "./InfrastructureHealthWidget";

const mockGet = api.get as jest.Mock;
type Pending = { resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

const healthy = { services: [{ name: "storage", status: "healthy" }] };

async function settle(fn: () => void) {
  await act(async () => {
    fn();
    await Promise.resolve();
  });
}

async function tick(ms: number) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
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

test("a slow health read is not overlapped by the next poll", async () => {
  render(<InfrastructureHealthWidget />);
  await tick(300_000);
  expect(mockGet).toHaveBeenCalledTimes(1);
  await settle(() => pending[0].resolve(healthy));
  await tick(60_000);
  expect(mockGet).toHaveBeenCalledTimes(2);
});

test("a failed background refresh keeps the last health visible, marked stale, and Retry recovers it", async () => {
  render(<InfrastructureHealthWidget />);
  await settle(() => pending[0].resolve(healthy));
  await tick(60_000);
  await settle(() => pending[1].reject(new Error("502")));

  expect(screen.getByText("Storage")).toBeInTheDocument();
  expect(screen.getByTestId("widget-stale")).toHaveTextContent(/could not be refreshed/i);

  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await settle(() => pending[2].resolve({ services: [{ name: "storage", status: "degraded" }] }));
  expect(screen.queryByTestId("widget-stale")).not.toBeInTheDocument();
  expect(screen.getByText("Storage")).toBeInTheDocument();
});
