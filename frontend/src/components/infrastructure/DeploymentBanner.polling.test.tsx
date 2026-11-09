/**
 * The deployment banner's status poll (plan 13): reads never overlap, and a failed read is not a
 * finished deployment.
 */
import { render, screen, act } from "@testing-library/react";

jest.mock("next/link", () => {
  return function MockLink({ children, href }: { children: React.ReactNode; href: string }) {
    return <a href={href}>{children}</a>;
  };
});
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import { api } from "@/lib/api";
import { DeploymentBanner } from "./DeploymentBanner";

const mockGet = api.get as jest.Mock;
type Pending = { resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

const status = (activeRunId: number | null, module: string | null = null) => ({
  terraform_initialized: true,
  gcp_credentials_configured: true,
  active_run_id: activeRunId,
  active_run_status: activeRunId ? "applying" : null,
  last_completed_module: module,
});

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

test("a slow status read is not overlapped by the next poll", async () => {
  render(<DeploymentBanner />);
  await tick(60_000);
  expect(mockGet).toHaveBeenCalledTimes(1);
  await settle(() => pending[0].resolve(status(42)));
  await tick(10_000);
  expect(mockGet).toHaveBeenCalledTimes(2);
});

test("a failed read during a deployment keeps the banner and announces no completion", async () => {
  render(<DeploymentBanner />);
  await settle(() => pending[0].resolve(status(42)));
  expect(screen.getByTestId("deployment-banner")).toBeInTheDocument();

  await tick(10_000);
  await settle(() => pending[1].reject(new Error("502")));

  expect(screen.getByTestId("deployment-banner")).toHaveTextContent(/could not be refreshed/i);
  expect(screen.queryByTestId("deployment-toast")).not.toBeInTheDocument();

  await tick(10_000);
  await settle(() => pending[2].resolve(status(null, "compute")));
  expect(screen.queryByTestId("deployment-banner")).not.toBeInTheDocument();
  expect(screen.getByText(/Compute cluster successfully deployed/)).toBeInTheDocument();
});

test("the completion notice is shown once, not on every later poll", async () => {
  render(<DeploymentBanner />);
  await settle(() => pending[0].resolve(status(42)));
  await tick(10_000);
  await settle(() => pending[1].resolve(status(null, "storage")));
  expect(screen.getByTestId("deployment-toast")).toBeInTheDocument();

  await tick(10_000);
  await settle(() => pending[2].resolve(status(null, "storage")));
  await tick(10_000);
  await settle(() => pending[3].resolve(status(null, "storage")));
  expect(screen.queryByTestId("deployment-toast")).not.toBeInTheDocument();
});
