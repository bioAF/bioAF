/**
 * The plot archive searched on every keystroke and accepted answers in arrival order (plan 13).
 * Search now waits for a pause, the grid is keyed by query, page, experiment, run, and dates, and
 * only the current key's answer is shown.
 */
import { render, screen, fireEvent, act } from "@testing-library/react";

jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true }));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn() },
  fileContentUrl: async () => "",
  plotThumbnailContentUrl: async () => "",
}));
jest.mock("@/hooks/useContentUrl", () => ({
  useFileContentUrl: () => null,
  usePlotThumbnailContentUrl: () => null,
}));

import PlotArchivePage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
type Pending = { url: string; resolve: (v: unknown) => void; reject: (e: unknown) => void };
let pending: Pending[];

function plot(id: number, title: string) {
  return {
    id, title, file: null, experiment_id: null, experiment_name: null, project_name: null,
    pipeline_run_id: null, pipeline_run_name: null, notebook_session_id: null,
    notebook_session_type: null, source_type: "pipeline", tags: [], thumbnail_url: null,
    indexed_at: "2026-01-01T00:00:00Z",
  };
}

function take(fragment: string): Pending {
  const index = pending.findIndex((p) => p.url.includes(fragment));
  if (index < 0) throw new Error(`no pending request matching ${fragment}: ${pending.map((p) => p.url)}`);
  return pending.splice(index, 1)[0];
}

function answer(fragment: string, titles: string[]) {
  const p = take(fragment);
  return act(async () => {
    p.resolve({ plots: titles.map((t, i) => plot(i + 1, t)), total: titles.length });
  });
}

function fail(fragment: string) {
  const p = take(fragment);
  return act(async () => {
    p.reject(new Error("502"));
  });
}

async function pause() {
  await act(async () => {
    jest.advanceTimersByTime(300);
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  pending = [];
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/experiments")) return Promise.resolve({ experiments: [{ id: 1, name: "Exp" }] });
    if (url.startsWith("/api/pipeline-runs")) return Promise.resolve({ runs: [] });
    return new Promise((resolve, reject) => pending.push({ url, resolve, reject }));
  });
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});

test("search waits for a pause", async () => {
  render(<PlotArchivePage />);
  await answer("/api/plots?", ["umap"]);

  fireEvent.change(screen.getByLabelText("Search"), { target: { value: "u" } });
  fireEvent.change(screen.getByLabelText("Search"), { target: { value: "um" } });
  await pause();

  const searches = mockGet.mock.calls.map((c) => c[0] as string).filter((u) => u.includes("query="));
  expect(searches).toEqual(["/api/plots?page=1&page_size=24&query=um"]);
});

test("an older search's late answer does not replace the newer one", async () => {
  render(<PlotArchivePage />);
  await answer("/api/plots?", ["umap", "heatmap"]);

  fireEvent.change(screen.getByLabelText("Search"), { target: { value: "u" } });
  await pause();
  fireEvent.change(screen.getByLabelText("Search"), { target: { value: "heat" } });
  await pause();
  await answer("query=heat", ["heatmap"]);
  await answer("query=u", ["umap", "tsne-umap"]);

  expect(screen.getAllByText("heatmap").length).toBeGreaterThan(0);
  expect(screen.queryByText("tsne-umap")).not.toBeInTheDocument();
});

test("a new experiment filter does not present the previous plots as current", async () => {
  render(<PlotArchivePage />);
  await answer("/api/plots?", ["umap"]);
  await act(async () => {});

  fireEvent.change(screen.getByLabelText("Experiment"), { target: { value: "1" } });

  expect(screen.queryByText("umap")).not.toBeInTheDocument();
  await answer("experiment_id=1", ["exp plot"]);
  expect(screen.getAllByText("exp plot").length).toBeGreaterThan(0);
});

test("an initial failure is an error with retry, not an empty archive", async () => {
  render(<PlotArchivePage />);
  await fail("/api/plots?");
  expect(screen.getByTestId("error-message")).toHaveTextContent(/plots could not be loaded/i);

  fireEvent.click(screen.getByTestId("error-retry"));
  await answer("/api/plots?", ["umap"]);
  expect(screen.queryByTestId("error-message")).not.toBeInTheDocument();
});
