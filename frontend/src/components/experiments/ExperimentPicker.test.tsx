/**
 * The shared experiment picker (issues #104, #105): every eligible experiment is reachable, a page at a time,
 * by name search and Previous/Next, with the project filter applied by the server. A selection that is not
 * on the current page keeps its name (read by id). Loading, empty, denied and failed states are explicit, and
 * a failed page can be retried.
 */
import { act, fireEvent, render, screen, waitFor } from "@/testing/renderWithProviders";
import { useState } from "react";

const mockGet = jest.fn();
jest.mock("@/lib/api", () => ({ api: { get: (...args: unknown[]) => mockGet(...args) } }));

import { ExperimentPicker } from "./ExperimentPicker";

const exp = (id: number, projectId: number | null = null) => ({
  id,
  name: `Exp ${id}`,
  code: null,
  project: projectId ? { id: projectId, name: `P${projectId}` } : null,
});

/** 250 experiments, newest (highest id) first, served 100 a page; `name` and `project_id` filter. */
function serveMany(total = 250) {
  const all = Array.from({ length: total }, (_, i) => exp(total - i, (total - i) % 2 ? 1 : 2));
  mockGet.mockImplementation((url: string) => {
    const u = new URL(url, "http://x");
    if (/^\/api\/experiments\/\d+$/.test(u.pathname)) {
      const id = Number(u.pathname.split("/").pop());
      const found = all.find((e) => e.id === id);
      return found ? Promise.resolve(found) : Promise.reject(Object.assign(new Error("nf"), { status: 404 }));
    }
    const name = u.searchParams.get("name") ?? "";
    const project = u.searchParams.get("project_id");
    const page = Number(u.searchParams.get("page"));
    const size = Number(u.searchParams.get("page_size"));
    const matching = all.filter(
      (e) => e.name.toLowerCase().includes(name.toLowerCase()) && (!project || String(e.project?.id) === project),
    );
    return Promise.resolve({ experiments: matching.slice((page - 1) * size, page * size), total: matching.length, page, page_size: size });
  });
}

function Harness({ initial = null, projectId = null }: { initial?: number | null; projectId?: number | null }) {
  const [value, setValue] = useState<number | null>(initial);
  const [picked, setPicked] = useState<string>("");
  return (
    <>
      <ExperimentPicker
        label="Experiment"
        value={value}
        projectId={projectId}
        onChange={(id, experiment) => {
          setValue(id);
          setPicked(experiment ? `${experiment.id}:${experiment.project?.id ?? "none"}` : "");
        }}
      />
      <output aria-label="picked">{picked}</output>
    </>
  );
}

const select = () => screen.getByRole("combobox", { name: "Experiment" });
const options = () => Array.from((select() as HTMLSelectElement).options).map((o) => o.textContent);

beforeEach(() => {
  mockGet.mockReset();
  jest.useFakeTimers();
});
afterEach(() => jest.useRealTimers());

async function settle() {
  await act(async () => {
    jest.advanceTimersByTime(400);
    for (let n = 0; n < 20; n++) await Promise.resolve();
  });
}

test("pages reach experiments past the first hundred and the first five hundred", async () => {
  serveMany(620);
  render(<Harness />);
  await settle();
  expect(screen.getByText("1–100 of 620")).toBeInTheDocument();
  for (let i = 0; i < 6; i++) {
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await settle();
  }
  expect(screen.getByText("601–620 of 620")).toBeInTheDocument();
  expect(options()).toContain("Exp 1");
  expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  fireEvent.change(select(), { target: { value: "1" } });
  expect(screen.getByLabelText("picked")).toHaveTextContent("1:1");
});

test("every request asks for at most one page of a hundred", async () => {
  serveMany();
  render(<Harness />);
  await settle();
  for (const [url] of mockGet.mock.calls) expect(String(url)).toMatch(/page_size=100(&|$)/);
});

test("search matches by name on the server and starts from its first page", async () => {
  serveMany();
  render(<Harness />);
  await settle();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  await settle();
  fireEvent.change(screen.getByRole("searchbox", { name: "Search names" }), { target: { value: "Exp 12" } });
  await settle();
  const last = String(mockGet.mock.calls.at(-1)?.[0]);
  expect(last).toContain("name=Exp+12");
  expect(last).toContain("page=1&");
  expect(options()).toContain("Exp 125");
});

test("the project filter is applied by the server", async () => {
  serveMany();
  render(<Harness projectId={2} />);
  await settle();
  expect(String(mockGet.mock.calls.at(-1)?.[0])).toContain("project_id=2");
  expect(screen.getByText("1–100 of 125")).toBeInTheDocument();
});

test("a selection beyond the current page keeps its name", async () => {
  serveMany();
  render(<Harness initial={3} />);
  await settle();
  expect(options()).toContain("Exp 3");
  expect(select()).toHaveValue("3");
});

test("an empty result says so", async () => {
  serveMany();
  render(<Harness />);
  await settle();
  fireEvent.change(screen.getByRole("searchbox", { name: "Search names" }), { target: { value: "zzz" } });
  await settle();
  expect(screen.getByText("No experiments match.")).toBeInTheDocument();
});

test("a failed page says so and can be retried", async () => {
  serveMany();
  const ok = mockGet.getMockImplementation()!;
  mockGet.mockImplementation(() => Promise.reject(new Error("boom")));
  render(<Harness />);
  await settle();
  expect(screen.getByText(/could not be loaded/)).toBeInTheDocument();
  mockGet.mockImplementation(ok);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await settle();
  await waitFor(() => expect(screen.getByText("1–100 of 250")).toBeInTheDocument());
});

test("no access says so instead of an empty list", async () => {
  mockGet.mockImplementation(() => Promise.reject(Object.assign(new Error("forbidden"), { status: 403 })));
  render(<Harness />);
  await settle();
  expect(screen.getByText(/do not have access to experiments/)).toBeInTheDocument();
});

test("a session ending drops the experiments shown", async () => {
  const { startSession, endSession } = jest.requireActual("@/lib/session");
  startSession("first.jwt");
  serveMany(3);
  render(<Harness />);
  await settle();
  expect(options()).toContain("Exp 3");
  mockGet.mockImplementation(() => new Promise(() => {}));
  await act(async () => endSession());
  await settle();
  expect(options()).not.toContain("Exp 3");
});
