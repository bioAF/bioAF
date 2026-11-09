/**
 * Publications are re-read every 5 s while one is publishing (plan 13). Reads must not overlap,
 * polling stops once nothing is publishing, a failed background read keeps the list but says it
 * may be out of date, and an older filter's answer cannot replace the current filter's.
 */
import { render, screen, fireEvent, act } from "@/testing/renderWithProviders";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  usePathname: () => "/results/cellxgene",
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn(), delete: jest.fn() } }));

import CellxgenePage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

function pub(id: number, status: string, experiment_id: number | null = 1) {
  return {
    id, dataset_name: `Dataset ${id}`, status, experiment_id, access_url: null, file: null,
    published_by: null, published_at: null,
  };
}

function publicationReads() {
  return pending.filter((p) => p.url.startsWith("/api/cellxgene"));
}

async function answer(url: string, value: unknown) {
  const i = pending.findIndex((p) => p.url === url);
  if (i < 0) throw new Error(`no pending ${url}`);
  const [p] = pending.splice(i, 1);
  await act(async () => p.resolve(value));
}

async function fail(url: string) {
  const i = pending.findIndex((p) => p.url === url);
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
  mockGet.mockImplementation((url: string) =>
    url.startsWith("/api/experiments")
      ? Promise.resolve({ experiments: [{ id: 1, name: "Exp A" }, { id: 2, name: "Exp B" }] })
      : new Promise((resolve, reject) => pending.push({ url, resolve, reject })),
  );
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("a slow read while publishing is never overlapped by the next poll", async () => {
  render(<CellxgenePage />);
  await answer("/api/cellxgene", [pub(1, "publishing")]);
  await tick(5000);
  expect(publicationReads()).toHaveLength(1);

  await tick(20_000);
  expect(publicationReads()).toHaveLength(1);
});

test("polling stops once nothing is publishing", async () => {
  render(<CellxgenePage />);
  await answer("/api/cellxgene", [pub(1, "publishing")]);
  await tick(5000);
  await answer("/api/cellxgene", [pub(1, "published")]);

  await tick(30_000);
  expect(publicationReads()).toHaveLength(0);
});

test("a failed background read keeps the list and says it may be out of date", async () => {
  render(<CellxgenePage />);
  await answer("/api/cellxgene", [pub(1, "publishing")]);
  await tick(5000);
  await fail("/api/cellxgene");

  expect(screen.getByText("Dataset 1")).toBeInTheDocument();
  expect(screen.getByTestId("stale-notice")).toBeInTheDocument();
});

test("an older filter's late answer does not replace the current filter's", async () => {
  render(<CellxgenePage />);
  await answer("/api/cellxgene", [pub(1, "published", 1), pub(2, "published", 2)]);

  fireEvent.change(screen.getByLabelText("Filter by experiment"), { target: { value: "1" } });
  fireEvent.change(screen.getByLabelText("Filter by experiment"), { target: { value: "2" } });
  await answer("/api/cellxgene?experiment_id=2", [pub(2, "published", 2)]);
  await answer("/api/cellxgene?experiment_id=1", [pub(1, "published", 1)]);

  expect(screen.getByText("Dataset 2")).toBeInTheDocument();
  expect(screen.queryByText("Dataset 1")).not.toBeInTheDocument();
});
