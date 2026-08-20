/**
 * What the launch wizard TELLS THE SERVER about the sheet it is showing.
 *
 * Found by driving the deployed demo: a scientist declared a column, the editor
 * said "they are emitted in this order", the review said "the samplesheet this
 * run will submit", and the sheet shown and submitted was bioAF's standard
 * three. The declaration bound a LATER run, and only if they also pressed
 * "Save for next time", a button presented as being about next time.
 *
 * Every unit test and `tsc` passed through it, because nothing here is about
 * rendering. It is about what goes in the request body, which is why these
 * assert request bodies.
 *
 * The three-way distinction is the whole of it:
 *
 *     absent  -> whatever is saved. The wizard has not read the editor yet
 *     []      -> nothing in force. The scientist cleared it
 *     [...]   -> this, for this run, saved or not
 */

import { render, screen, waitFor, fireEvent } from "@/testing/renderWithProviders";

const mockRouter = { push: jest.fn() };
jest.mock("next/navigation", () => ({
  useRouter: () => mockRouter,
  useParams: () => ({ key: "nf-core%2Fmcmicro" }),
  useSearchParams: () => new URLSearchParams(),
}));

jest.mock("@/lib/auth", () => ({ isAuthenticated: () => true, getToken: () => "fake-token" }));

jest.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(
      public status: number,
      message: string,
      public code?: string,
      public details?: Record<string, unknown>,
    ) {
      super(message);
      this.name = "ApiError";
    }
  }
  return { api: { get: jest.fn(), post: jest.fn() }, ApiError };
});

jest.mock("@/components/shared/ContentLoading", () => ({ ContentLoading: () => null }));

import LaunchPage from "./page";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

const EXPERIMENT = { id: 7, name: "Imaging", status: "registered", sample_count: 1 };
const SAMPLE = { id: 3, external_id: "SLIDE-1", organism: "Homo sapiens", qc_status: null, status: "registered" };

/** A pipeline that publishes no contract, which is the only population where a
 *  declaration means anything. */
function preflight(overrides: Record<string, unknown> = {}) {
  return {
    can_launch: true,
    code: null,
    reason: null,
    details: {},
    samplesheet: { columns: ["sample", "fastq_1", "fastq_2"], rows: [], csv: "sample,fastq_1,fastq_2\n", omissions: [] },
    per_sample_inputs: [],
    declaration: { declarable: true, file_types: ["tiff"], custom_fields: [] },
    prefill: { scope: null, values: {}, bindings: {}, columns: [], samples_without_values: [] },
    ...overrides,
  };
}

function preflightBodies() {
  return mockPost.mock.calls
    .filter(([url]) => url === "/api/pipeline-runs/preflight")
    .map(([, body]) => body);
}

beforeEach(() => {
  jest.clearAllMocks();
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/pipelines/")) {
      return Promise.resolve({ pipeline_key: "nf-core/mcmicro", name: "mcmicro", default_params: null, parameter_schema: null });
    }
    if (url.startsWith("/api/experiments/")) return Promise.resolve([SAMPLE]);
    if (url.startsWith("/api/experiments")) return Promise.resolve({ experiments: [EXPERIMENT], total: 1 });
    return Promise.resolve({});
  });
  mockPost.mockImplementation((url: string) => {
    if (url === "/api/pipeline-runs/preflight") return Promise.resolve(preflight());
    if (url === "/api/pipeline-runs") return Promise.resolve({ id: 99 });
    return Promise.resolve({});
  });
});

/** Through the wizard as far as the Values step, where the editor lives. */
async function toValuesStep() {
  render(<LaunchPage />);
  await waitFor(() => expect(screen.getByRole("combobox")).toBeInTheDocument());
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "7" } });
  await waitFor(() => expect(preflightBodies().length).toBeGreaterThan(0));
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(screen.getByText("SLIDE-1")).toBeInTheDocument());
  // Every loaded sample arrives already selected, so this step is a
  // confirmation rather than a choice. Clicking the box would DEselect it and
  // leave Next disabled.
  await waitFor(() => expect(screen.getByRole("button", { name: "Next" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(screen.getByText("Samplesheet columns")).toBeInTheDocument());
}

describe("what the wizard tells the server about the sheet", () => {
  it("says nothing about columns before it has read the editor", async () => {
    /** The very first preflight. `null` is "we have not been told yet", and it
     *  must reach the server as SILENCE: sending [] would preview a generic
     *  sheet for an experiment that has a declaration saved, and the editor
     *  would then adopt that emptiness as the truth. */
    render(<LaunchPage />);
    await waitFor(() => expect(screen.getByRole("combobox")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "7" } });

    await waitFor(() => expect(preflightBodies().length).toBeGreaterThan(0));
    expect(preflightBodies()[0]).not.toHaveProperty("columns");
  });

  it("carries the declaration on screen once the editor holds one", async () => {
    await toValuesStep();
    const before = preflightBodies().length;

    fireEvent.click(screen.getByRole("button", { name: "Start from the standard sheet" }));

    await waitFor(() => expect(preflightBodies().length).toBeGreaterThan(before));
    const latest = preflightBodies()[preflightBodies().length - 1];
    expect(latest.columns.map((c: { name: string }) => c.name)).toEqual(["sample", "fastq_1", "fastq_2"]);
  });

  it("re-asks when a column changes, so the review cannot confirm a stale sheet", async () => {
    await toValuesStep();
    fireEvent.click(screen.getByRole("button", { name: "Start from the standard sheet" }));
    await waitFor(() => expect(preflightBodies().length).toBeGreaterThan(2));
    const before = preflightBodies().length;

    fireEvent.change(screen.getByLabelText("Column name", { selector: "#column-name-0" }), {
      target: { value: "slide" },
    });

    await waitFor(() => expect(preflightBodies().length).toBeGreaterThan(before));
    const latest = preflightBodies()[preflightBodies().length - 1];
    expect(latest.columns[0].name).toBe("slide");
  });

  it("submits the declaration the review step just showed", async () => {
    await toValuesStep();
    fireEvent.click(screen.getByRole("button", { name: "Start from the standard sheet" }));
    await waitFor(() => expect(preflightBodies().length).toBeGreaterThan(2));

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Launch Pipeline|Next/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Launch Pipeline" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Launch Pipeline" }));

    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/pipeline-runs", expect.anything()));
    const body = mockPost.mock.calls.find(([url]) => url === "/api/pipeline-runs")![1];
    expect(body.columns.map((c: { name: string }) => c.name)).toEqual(["sample", "fastq_1", "fastq_2"]);
  });

  it("does not save the declaration by launching with it", async () => {
    /** Nothing is promoted by launching (design 02 section 4). A one-off
     *  accommodation must never become what the next person inherits, so
     *  carrying a declaration to THIS run must not quietly write it down for the
     *  next one. Driven all the way through Launch, because the write it is
     *  guarding against would happen at the end. */
    await toValuesStep();
    fireEvent.click(screen.getByRole("button", { name: "Start from the standard sheet" }));
    // Column names live in input values, not in text, so wait on the editor's
    // effect instead: the preflight that carries the declaration.
    await waitFor(() =>
      expect(preflightBodies().some((b) => b.columns?.length === 3)).toBe(true),
    );

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Next" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Launch Pipeline" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Launch Pipeline" }));

    await waitFor(() => expect(mockRouter.push).toHaveBeenCalledWith("/pipelines/runs/99"));
    expect(mockPost.mock.calls.some(([url]) => url === "/api/samplesheet-mappings")).toBe(false);
  });
});
