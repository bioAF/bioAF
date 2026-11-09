/**
 * A run's metadata (every 10 s) and logs (every 5 s) are re-read while it is active (plan 13).
 * The intervals could overlap slow reads, a previously selected process's late logs could replace
 * the current process's, a failed refresh left the page looking current, and a first logs read
 * that failed rendered as "(empty)".
 */
import { render, screen, fireEvent, act } from "@/testing/renderWithProviders";

const mockRouter = { push: jest.fn() };
const mockParams = { id: "1" };
jest.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useParams: () => mockParams,
}));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  api: { get: jest.fn(), post: jest.fn() },
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, roleName: "admin", loading: false, permissions: new Set() }),
}));
jest.mock("@/components/experiments/ReviewPanel", () => ({ ReviewPanel: () => null }));

import PipelineRunDetailPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

const RUN = "/api/pipeline-runs/1";

function run(status: string, extra: Record<string, unknown> = {}) {
  return {
    id: 1, status, compute_job_ref: "bioaf-pipeline-1", pipeline_name: "nf-core/scrnaseq",
    custom_pipeline_version_id: null, organization_id: 1, processes: [], parameters: {},
    ...extra,
  };
}

function reads(url: string) {
  return pending.filter((p) => p.url === url);
}

async function answer(url: string, value: unknown) {
  const i = pending.findIndex((p) => p.url === url);
  if (i < 0) throw new Error(`no pending ${url}`);
  const [p] = pending.splice(i, 1);
  await act(async () => p.resolve(value));
}

async function fail(url: string) {
  const i = pending.findIndex((p) => p.url === url);
  if (i < 0) throw new Error(`no pending ${url}`);
  const [p] = pending.splice(i, 1);
  await act(async () => p.reject(new Error("502")));
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
  mockGet.mockImplementation((url: string) => {
    if (url.endsWith("/references")) return Promise.resolve([]);
    return new Promise((resolve, reject) => pending.push({ url, resolve, reject }));
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("slow log and run reads are never overlapped by the next poll", async () => {
  render(<PipelineRunDetailPage />);
  await answer(RUN, run("running"));
  await answer(`${RUN}/logs`, { stdout: "line 1", stderr: "" });

  await tick(5000);
  expect(reads(`${RUN}/logs`)).toHaveLength(1);
  await tick(10_000);
  expect(reads(RUN)).toHaveLength(1);

  await tick(60_000);
  expect(reads(`${RUN}/logs`)).toHaveLength(1);
  expect(reads(RUN)).toHaveLength(1);
});

test("polling stops once the run is finished", async () => {
  render(<PipelineRunDetailPage />);
  await answer(RUN, run("running"));
  await answer(`${RUN}/logs`, { stdout: "line 1", stderr: "" });
  await tick(10_000);
  await answer(RUN, run("completed"));
  // One last read of the logs for the finished run, then nothing.
  for (const p of reads(`${RUN}/logs`)) await answer(p.url, { stdout: "done", stderr: "" });

  await tick(60_000);
  expect(reads(RUN)).toHaveLength(0);
  expect(reads(`${RUN}/logs`)).toHaveLength(0);
});

test("the previous process's late logs do not replace the selected process's", async () => {
  render(<PipelineRunDetailPage />);
  await answer(RUN, run("running", {
    compute_job_ref: null,
    processes: [{ id: 1, process_name: "ALIGN" }, { id: 2, process_name: "QC" }],
  }));

  fireEvent.change(screen.getByLabelText("Selected process"), { target: { value: "ALIGN" } });
  fireEvent.change(screen.getByLabelText("Selected process"), { target: { value: "QC" } });
  await answer(`${RUN}/logs/QC`, { stdout: "qc output", stderr: "" });
  await answer(`${RUN}/logs/ALIGN`, { stdout: "align output", stderr: "" });

  expect(screen.getByText("qc output")).toBeInTheDocument();
  expect(screen.queryByText("align output")).not.toBeInTheDocument();
});

test("a failed background run refresh keeps the run and says it may be out of date", async () => {
  render(<PipelineRunDetailPage />);
  await answer(RUN, run("running"));
  await answer(`${RUN}/logs`, { stdout: "line 1", stderr: "" });
  await tick(10_000);
  await fail(RUN);

  expect(screen.getByTestId("stale-notice")).toHaveTextContent(/this run/i);
  expect(screen.getByText("line 1")).toBeInTheDocument();
});

test("a first logs read that fails is reported, not shown as empty logs", async () => {
  render(<PipelineRunDetailPage />);
  await answer(RUN, run("completed"));
  await fail(`${RUN}/logs`);

  expect(screen.getByText(/logs could not be loaded/i)).toBeInTheDocument();
  expect(screen.queryByText("(empty)")).not.toBeInTheDocument();
});

test("background log refreshes keep the log view mounted", async () => {
  render(<PipelineRunDetailPage />);
  await answer(RUN, run("running"));
  await answer(`${RUN}/logs`, { stdout: "line 1", stderr: "" });
  const pre = screen.getByText("line 1");

  await tick(5000);
  expect(screen.getByText("line 1")).toBe(pre);
  await answer(`${RUN}/logs`, { stdout: "line 1\nline 2", stderr: "" });
  expect(screen.getByText(/line 2/)).toBe(pre);
});
