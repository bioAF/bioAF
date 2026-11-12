/**
 * The experiment detail page belongs to one experiment at a time. When the route moves to another
 * experiment, nothing from the first (its record, a tab's rows, an edit in progress, a selection,
 * a failure, or a loading state) may show up on the second, however the first's reads settle.
 * Reads go through the shared resource lifecycle, so each forwards a cancellation signal.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@/testing/renderWithProviders";
import ExperimentDetailPage from "./page";
import AliasPage from "@/app/(app)/projects/experiments/[id]/page";

jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, email: "a@b.c", role_name: "admin" }),
  getToken: () => "t",
}));

let mockId = "1";
let mockQuery = "";
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  useParams: () => ({ id: mockId }),
  useSearchParams: () => new URLSearchParams(mockQuery),
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, loading: false }),
}));
jest.mock("@/components/shared/VocabularySelect", () => ({ VocabularySelect: () => <select /> }));
jest.mock("@/components/SnapshotTimeline", () => ({ __esModule: true, default: () => <div /> }));
jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  api: { get: jest.fn(), post: jest.fn(), patch: jest.fn(), delete: jest.fn() },
}));

import { api } from "@/lib/api";
const mockGet = api.get as jest.Mock;
const mockPatch = api.patch as jest.Mock;

function experiment(id: number, name: string) {
  return {
    id,
    name,
    status: "registered",
    hypothesis: null,
    description: null,
    start_date: null,
    expected_sample_count: null,
    project: null,
    template_name: null,
    owner: null,
    sample_count: 1,
    custom_fields: [],
    field_defaults: [],
    created_at: "2026-03-10T00:00:00Z",
  };
}

function sample(id: number, externalId: string) {
  return {
    id,
    external_id: externalId,
    organism: null,
    tissue_type: null,
    molecule_type: null,
    treatment_condition: null,
    library_prep_method: null,
    library_layout: null,
    file_count: 0,
    qc_status: null,
    status: "registered",
    custom_fields: [],
    created_at: "2026-03-10T00:00:00Z",
    updated_at: "2026-03-10T00:00:00Z",
  };
}

type Reply = { resolve: (v: unknown) => void; reject: (e: unknown) => void };
let held: Record<string, Reply[]>;
let signals: Record<string, AbortSignal[]>;

/** Answers immediately from `answers`, or holds a reply for any URL listed in `hold`. */
function serve(answers: Record<string, unknown>, hold: string[] = []) {
  mockGet.mockImplementation((url: string, controls?: { signal?: AbortSignal }) => {
    (signals[url] ??= []).push(controls?.signal as AbortSignal);
    if (hold.includes(url)) {
      return new Promise((resolve, reject) => (held[url] ??= []).push({ resolve, reject }));
    }
    if (url in answers) {
      const answer = answers[url];
      return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer);
    }
    if (url.startsWith("/api/naming-profiles")) return Promise.resolve([]);
    return Promise.resolve([]);
  });
}

beforeEach(() => {
  mockId = "1";
  mockQuery = "";
  held = {};
  signals = {};
  mockGet.mockReset();
  mockPatch.mockReset();
  jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => jest.restoreAllMocks());

test("the alias route is the same page", () => {
  expect(AliasPage).toBe(ExperimentDetailPage);
});

test("an earlier experiment's record that settles late is never shown for the new one", async () => {
  serve({ "/api/experiments/2": experiment(2, "Second") }, ["/api/experiments/1"]);
  const view = render(<ExperimentDetailPage />);

  mockId = "2";
  view.rerender(<ExperimentDetailPage />);
  expect(await screen.findByRole("heading", { name: "Second" })).toBeInTheDocument();

  await act(async () => held["/api/experiments/1"][0].resolve(experiment(1, "First")));
  expect(screen.queryByText("First")).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Second" })).toBeInTheDocument();
});

test("an earlier experiment's failure cannot end the new one's loading state", async () => {
  serve({}, ["/api/experiments/1", "/api/experiments/2"]);
  const view = render(<ExperimentDetailPage />);
  mockId = "2";
  view.rerender(<ExperimentDetailPage />);

  await act(async () => held["/api/experiments/1"][0].reject(Object.assign(new Error("gone"), { status: 404 })));
  expect(screen.queryByText("Experiment not found")).not.toBeInTheDocument();
  expect(screen.queryByTestId("experiment-load-failed")).not.toBeInTheDocument();

  await act(async () => held["/api/experiments/2"][0].resolve(experiment(2, "Second")));
  expect(screen.getByRole("heading", { name: "Second" })).toBeInTheDocument();
});

test("a tab's rows for the earlier experiment never appear on the new one", async () => {
  mockQuery = "tab=samples";
  serve(
    {
      "/api/experiments/1": experiment(1, "First"),
      "/api/experiments/2": experiment(2, "Second"),
      "/api/experiments/2/samples": [sample(20, "S-second")],
    },
    ["/api/experiments/1/samples"],
  );
  const view = render(<ExperimentDetailPage />);
  await screen.findByRole("heading", { name: "First" });

  mockId = "2";
  view.rerender(<ExperimentDetailPage />);
  expect(await screen.findByText("S-second")).toBeInTheDocument();

  await act(async () => held["/api/experiments/1/samples"][0].resolve([sample(10, "S-first")]));
  expect(screen.queryByText("S-first")).not.toBeInTheDocument();
});

test("an edit in progress and a selection stay with their experiment", async () => {
  mockQuery = "tab=samples";
  serve({
    "/api/experiments/1": experiment(1, "First"),
    "/api/experiments/2": experiment(2, "Second"),
    "/api/experiments/1/samples": [sample(10, "S-first")],
    "/api/experiments/2/samples": [sample(20, "S-second")],
  });
  const view = render(<ExperimentDetailPage />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "Select sample S-first" }));
  expect(screen.getByRole("button", { name: "Edit Selected (1)" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Add Sample" }));
  expect(screen.getByRole("form", { name: "Add sample" })).toBeInTheDocument();

  mockId = "2";
  view.rerender(<ExperimentDetailPage />);
  expect(await screen.findByText("S-second")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Edit Selected/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("form", { name: "Add sample" })).not.toBeInTheDocument();
});

test("an overview edit does not follow the route to another experiment", async () => {
  serve({ "/api/experiments/1": experiment(1, "First"), "/api/experiments/2": experiment(2, "Second") });
  const view = render(<ExperimentDetailPage />);
  await screen.findByRole("heading", { name: "First" });
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Renamed" } });

  mockId = "2";
  view.rerender(<ExperimentDetailPage />);
  await screen.findByRole("heading", { name: "Second" });
  expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
  expect(screen.queryByDisplayValue("Renamed")).not.toBeInTheDocument();
});

test("a failed refresh keeps the experiment on screen and says it may be out of date", async () => {
  let reads = 0;
  mockGet.mockImplementation((url: string) => {
    if (url === "/api/experiments/1") {
      reads += 1;
      return reads === 1 ? Promise.resolve(experiment(1, "First")) : Promise.reject(new Error("Backend unavailable"));
    }
    return Promise.resolve([]);
  });
  mockPatch.mockResolvedValue({});
  render(<ExperimentDetailPage />);
  await screen.findByRole("heading", { name: "First" });

  fireEvent.change(screen.getByRole("combobox", { name: "Change experiment status" }), {
    target: { value: "sequencing" },
  });
  expect(await screen.findByTestId("stale-notice")).toHaveTextContent("This experiment could not be refreshed");
  expect(screen.getByRole("heading", { name: "First" })).toBeInTheDocument();
});

test("the record and tab reads carry a signal that aborts when the page goes away", async () => {
  mockQuery = "tab=samples";
  serve({ "/api/experiments/1": experiment(1, "First") }, ["/api/experiments/1/samples"]);
  const view = render(<ExperimentDetailPage />);
  await screen.findByRole("heading", { name: "First" });
  await waitFor(() => expect(signals["/api/experiments/1/samples"]).toHaveLength(1));

  const samplesSignal = signals["/api/experiments/1/samples"][0];
  expect(signals["/api/experiments/1"][0]).toBeInstanceOf(AbortSignal);
  expect(samplesSignal.aborted).toBe(false);
  view.unmount();
  expect(samplesSignal.aborted).toBe(true);
});

test("Escape closes the sample editor once, through the dialog", async () => {
  mockQuery = "tab=samples";
  serve({ "/api/experiments/1": experiment(1, "First"), "/api/experiments/1/samples": [sample(10, "S-first")] });
  render(<ExperimentDetailPage />);
  await screen.findByText("S-first");
  const row = screen.getByText("S-first").closest("tr")!;
  fireEvent.click(within(row).getByRole("button", { name: "Edit" }));
  const dialog = screen.getByRole("dialog", { name: "Edit Sample" });
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "Edit Sample" })).not.toBeInTheDocument();
});
