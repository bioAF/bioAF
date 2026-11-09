/**
 * Watching an AI lit review run (plan 13). The watch belonged to a shared flag, so stopping one
 * run and starting another revived the first run's loop: its status overwrote the second run's
 * message and the completion callback fired for both from one loop. Each run's watch is now its
 * own, stopping it ends it, and each run notifies once.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

jest.mock("@/lib/auth", () => ({ getCurrentUser: () => ({ role_name: "admin" }) }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));
jest.mock("@/lib/literature", () => {
  const actual = jest.requireActual("@/lib/literature");
  return { ...actual, literature: { ...actual.literature, runLitReview: jest.fn(), getRun: jest.fn() } };
});

import { AiLitReviewLauncher } from "./AiLitReviewLauncher";
import { api } from "@/lib/api";
import { literature } from "@/lib/literature";

const mockGet = api.get as jest.Mock;
const mockRun = literature.runLitReview as jest.Mock;
const mockGetRun = literature.getRun as jest.Mock;

let status: Record<number, string>;

async function tick(ms: number) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
  });
}

async function startRun(id: number) {
  mockRun.mockResolvedValueOnce({ id, experiment_id: 103, status: "queued", recommendation_count: null });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /run ai lit review/i }));
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  status = {};
  mockGet.mockReset();
  mockRun.mockReset();
  mockGetRun.mockReset();
  mockGet.mockImplementation((url: string) =>
    url.includes("/api/experiments")
      ? Promise.resolve({ experiments: [{ id: 103, name: "Exp", project: null }], total: 1 })
      : Promise.resolve({ projects: [], total: 0 }),
  );
  mockGetRun.mockImplementation((id: number) =>
    Promise.resolve({ id, status: status[id] ?? "running", recommendation_count: 4 }),
  );
});
afterEach(() => jest.useRealTimers());

async function chooseExperiment() {
  await act(async () => {
    await Promise.resolve();
  });
  fireEvent.change(screen.getByLabelText(/experiment/i), { target: { value: "103" } });
}

test("a stopped run's watch stays stopped when another run starts, and each run notifies once", async () => {
  const onSubmitted = jest.fn();
  render(<AiLitReviewLauncher onSubmitted={onSubmitted} />);
  await chooseExperiment();

  await startRun(1);
  await tick(1000);
  fireEvent.click(screen.getByRole("button", { name: /stop watching/i }));
  await startRun(2);

  status[1] = "complete";
  await tick(3000);
  expect(screen.queryByText(/run 1 complete/)).not.toBeInTheDocument();
  const readsOfRun1 = mockGetRun.mock.calls.filter(([id]) => id === 1).length;
  expect(readsOfRun1).toBe(1);

  status[2] = "partial";
  await tick(1000);
  expect(screen.getByText(/run 2 partial; 4 papers added/)).toBeInTheDocument();
  expect(onSubmitted.mock.calls.map(([run]) => run.id)).toEqual([1, 2]);
});

test("unmounting stops the watch and notifies nobody", async () => {
  const onSubmitted = jest.fn();
  const { unmount } = render(<AiLitReviewLauncher onSubmitted={onSubmitted} />);
  await chooseExperiment();
  await startRun(1);
  await tick(1000);
  const reads = mockGetRun.mock.calls.length;

  unmount();
  await tick(120_000);

  expect(mockGetRun.mock.calls.length).toBe(reads);
  expect(onSubmitted).not.toHaveBeenCalled();
});

test("the wait stays bounded: after 60 reads the launcher stops waiting", async () => {
  const onSubmitted = jest.fn();
  render(<AiLitReviewLauncher onSubmitted={onSubmitted} />);
  await chooseExperiment();
  await startRun(1);

  for (let i = 0; i < 70; i++) await tick(1000);

  expect(mockGetRun).toHaveBeenCalledTimes(60);
  expect(screen.queryByTestId("lit-review-progress")).not.toBeInTheDocument();
  expect(onSubmitted).toHaveBeenCalledTimes(1);
});
