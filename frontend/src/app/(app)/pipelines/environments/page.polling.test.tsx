/**
 * Pipeline templates are re-read every 5 s while a build is running. The old interval
 * fired list and detail reads regardless of whether the previous ones had answered, swallowed a
 * failed list refresh, and a slow detail for one template could replace another the user had
 * since opened.
 */
import { render, screen, fireEvent, act } from "@/testing/renderWithProviders";

jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/auth", () => ({
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  isAuthenticated: () => true,
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn(), delete: jest.fn() } }));

import PipelineEnvironmentsPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

const LIST = "/api/v1/environments?type=pipeline";

function env(id: number, status: string) {
  return {
    id, name: `Template ${id}`, description: null, environment_type: "pipeline",
    latest_version: { id: id * 10, version_number: 1, status }, created_by: null,
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  };
}

function detail(id: number, status: string) {
  return {
    ...env(id, status),
    versions: [{ id: id * 10, version_number: 1, status, definition_format: "conda", created_at: "2026-01-01T00:00:00Z" }],
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
  mockGet.mockImplementation(
    (url: string) => new Promise((resolve, reject) => pending.push({ url, resolve, reject })),
  );
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("while a build runs, list reads never overlap, and they stop when it finishes", async () => {
  render(<PipelineEnvironmentsPage />);
  await answer(LIST, { environments: [env(1, "building")] });

  await tick(5000);
  expect(reads(LIST)).toHaveLength(1);
  await tick(30_000);
  expect(reads(LIST)).toHaveLength(1);

  await answer(LIST, { environments: [env(1, "ready")] });
  await tick(30_000);
  expect(reads(LIST)).toHaveLength(0);
});

test("a failed background list refresh is shown, and the list is kept", async () => {
  render(<PipelineEnvironmentsPage />);
  await answer(LIST, { environments: [env(1, "building")] });
  await tick(5000);
  await fail(LIST);

  expect(screen.getByText("Template 1")).toBeInTheDocument();
  expect(screen.getByTestId("stale-notice")).toBeInTheDocument();
});

test("a slow template detail does not replace the template opened after it", async () => {
  render(<PipelineEnvironmentsPage />);
  await answer(LIST, { environments: [env(1, "ready"), env(2, "ready")] });

  fireEvent.click(screen.getByText("Template 1"));
  fireEvent.click(screen.getByText("Template 2"));
  await answer("/api/v1/environments/2", detail(2, "ready"));
  await answer("/api/v1/environments/1", detail(1, "ready"));

  expect(screen.getByRole("heading", { name: "Template 2" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Template 1" })).not.toBeInTheDocument();
});

test("a failed refresh of an open template keeps it on screen and says it may be out of date", async () => {
  render(<PipelineEnvironmentsPage />);
  await answer(LIST, { environments: [env(1, "building")] });
  fireEvent.click(screen.getByText("Template 1"));
  await answer("/api/v1/environments/1", detail(1, "building"));

  await tick(5000);
  if (reads("/api/v1/environments/1").length === 0) await tick(5000);
  await fail("/api/v1/environments/1");

  expect(screen.getByRole("heading", { name: "Template 1" })).toBeInTheDocument();
  expect(screen.getByTestId("stale-notice")).toBeInTheDocument();
});

test("list and open-template reads carry a signal that aborts when the page closes", async () => {
  const { unmount } = render(<PipelineEnvironmentsPage />);
  await answer(LIST, { environments: [env(1, "building")] });
  fireEvent.click(screen.getByText("Template 1"));
  await answer("/api/v1/environments/1", detail(1, "building"));
  await tick(5000);
  if (reads("/api/v1/environments/1").length === 0) await tick(5000);

  const inFlight = pending
    .filter((p) => p.url === LIST || p.url === "/api/v1/environments/1")
    .map((p) => mockGet.mock.calls.filter(([url]) => url === p.url).at(-1)?.[1]?.signal);
  expect(inFlight.length).toBeGreaterThan(0);
  expect(inFlight.every((s) => s instanceof AbortSignal)).toBe(true);
  unmount();
  expect(inFlight.every((s) => s.aborted)).toBe(true);
});
