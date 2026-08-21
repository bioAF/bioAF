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

/**
 * A step the wizard is standing on must not be deleted underneath the user.
 *
 * `71d3f644` made the entry grid skip any gap that carries a remedy, which is
 * right on its own terms: a column no typed value can clear must not be offered
 * as a field. But the page decided whether the Values step EXISTS from the
 * length of that same list, so answering the question emptied the grid, dropped
 * "values" out of `steps` while `step` was still "values", and
 * `steps.indexOf("values")` returned -1. The clamp to 0 then turned Next into a
 * jump back to Select Samples.
 *
 * Measured in a browser on the deployed demo driving nf-core/ampliseq: the
 * panel still said "Values for each sample" while the indicator had silently
 * renumbered from five steps to four and highlighted Experiment. Every unit
 * test and `tsc` passed with it present, which is why these exist.
 */
describe("a Values step the user is standing on", () => {
  /** The gap a value CAN clear, so the grid offers it and the step exists. */
  const ASKABLE = {
    name: "condition",
    required: true,
    is_file: false,
    sample_field: null,
    allowed_values: [],
    constrained: false,
    description: null,
    format_hint: null,
    required_by: null,
    reason: null,
    samples: [{ id: 3, external_id: "SLIDE-1" }],
  };

  function askingForAValue() {
    return preflight({
      can_launch: false,
      reason: "condition is not something bioAF can derive.",
      declaration: { declarable: false, file_types: [], custom_fields: [] },
      per_sample_inputs: [ASKABLE],
      details: {
        missing_columns: {
          condition: { sample_field: null, allowed_values: [], samples: [{ id: 3, external_id: "SLIDE-1" }], reason: "missing" },
        },
      },
    });
  }

  /** ampliseq's shape once the first gap is answered: the rule is on the
   *  sample's own name ALONE, both rows carry it, and no typed value can
   *  separate them. The gap carries a remedy, so the grid is right to drop it
   *  and `per_sample_inputs` comes back empty. */
  function blockedOnARemedy() {
    return preflight({
      can_launch: false,
      reason:
        "This pipeline takes one row per sample, and some samples have more than one set of reads. Merge those reads, or launch them as separate samples.",
      declaration: { declarable: false, file_types: [], custom_fields: [] },
      per_sample_inputs: [],
      details: {
        missing_columns: {
          sample: {
            sample_field: null,
            allowed_values: [],
            samples: [{ id: 3, external_id: "SLIDE-1" }],
            reason: "not_unique",
            remedy: "one_row_per_sample",
          },
        },
      },
    });
  }

  /** The indicator's labels, in order. Each is EXACTLY the step's name, which
   *  is what tells them apart from the panel heading that repeats one. */
  function indicatorSteps(): string[] {
    return screen
      .getAllByText(/^(Experiment|Samples|Values|Parameters|Review)$/)
      .map((node) => node.textContent ?? "");
  }

  /** To Values on a pipeline that asks for one value, then answers it, which is
   *  the transition that used to delete the step. */
  async function answerTheOnlyQuestion() {
    mockPost.mockImplementation((url: string, body: { sample_values?: Record<string, unknown> }) => {
      if (url === "/api/pipeline-runs/preflight") {
        return Promise.resolve(
          Object.keys(body?.sample_values ?? {}).length > 0 ? blockedOnARemedy() : askingForAValue(),
        );
      }
      if (url === "/api/pipeline-runs") return Promise.resolve({ id: 99 });
      return Promise.resolve({});
    });

    render(<LaunchPage />);
    await waitFor(() => expect(screen.getByRole("combobox")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "7" } });
    await waitFor(() => expect(preflightBodies().length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(screen.getByText("SLIDE-1")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("button", { name: "Next" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(screen.getByText("Values for each sample")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("condition for SLIDE-1"), { target: { value: "treated" } });
    // The grid empties on the preflight that answer triggers.
    await waitFor(() => expect(screen.queryByLabelText("condition for SLIDE-1")).not.toBeInTheDocument());
  }

  it("keeps Values in the indicator when the only gap left carries a remedy", async () => {
    await answerTheOnlyQuestion();

    expect(screen.getByText("Values for each sample")).toBeInTheDocument();
    expect(indicatorSteps()).toEqual(["Experiment", "Samples", "Values", "Parameters", "Review"]);
  });

  it("does not walk backwards out of a Values step whose grid emptied", async () => {
    await answerTheOnlyQuestion();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => expect(screen.getByText("Configure Parameters")).toBeInTheDocument());
    expect(screen.queryByText("Select Samples")).not.toBeInTheDocument();
  });

  it("still refuses the launch, so the step surviving is not the run unblocking", async () => {
    await answerTheOnlyQuestion();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(screen.getByText("Configure Parameters")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Launch Pipeline" })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Launch Pipeline" })).toBeDisabled();
  });
});

/**
 * Issue #85: dropping the samples that have no input files.
 *
 * The preflight now refuses a selection containing one, which is what the launch
 * always did. That disables the Launch button, and the button was the only route
 * to the confirm dialog that offered to drop those samples and run with the rest.
 * So the offer moves to the block, and taking it re-asks the preflight with the
 * flag set: the review step then shows the sheet WITHOUT those rows, which is the
 * one property that step exists to provide.
 */
describe("a selection holding a sample with no input files", () => {
  function blockedOnFiles() {
    return preflight({
      can_launch: false,
      code: "samples_missing_files",
      reason: "Some selected samples have no linked input files",
      details: { samples_without_files: [{ id: 4, external_id: "SLIDE-2" }] },
    });
  }

  /** With the flag set the server drops them, so the run is launchable again. */
  function respondByFlag() {
    mockPost.mockImplementation((url: string, body: Record<string, unknown>) => {
      if (url === "/api/pipeline-runs/preflight") {
        return Promise.resolve(body.drop_samples_without_files ? preflight() : blockedOnFiles());
      }
      if (url === "/api/pipeline-runs") return Promise.resolve({ id: 99 });
      return Promise.resolve({});
    });
  }

  function lastPreflightBody() {
    const bodies = preflightBodies();
    return bodies[bodies.length - 1];
  }

  /** As far as the first step that renders the block. */
  async function toTheBlock() {
    respondByFlag();
    await toValuesStep();
    await waitFor(() => expect(screen.getByText(/no linked input files/i)).toBeInTheDocument());
  }

  async function takeTheDrop() {
    fireEvent.click(screen.getByRole("button", { name: /drop/i }));
    await waitFor(() => expect(screen.queryByText(/no linked input files/i)).not.toBeInTheDocument());
  }

  it("names the samples it would leave out", async () => {
    await toTheBlock();

    expect(screen.getByText(/SLIDE-2/)).toBeInTheDocument();
  });

  it("asks the preflight again with the samples dropped", async () => {
    await toTheBlock();

    await takeTheDrop();

    await waitFor(() => expect(lastPreflightBody().drop_samples_without_files).toBe(true));
  });

  it("launches under the same flag the sheet was reviewed with", async () => {
    await toTheBlock();
    await takeTheDrop();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(screen.getByText("Parameters")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    const launch = await screen.findByRole("button", { name: /Launch Pipeline/i });
    await waitFor(() => expect(launch).toBeEnabled());
    fireEvent.click(launch);

    await waitFor(() => {
      const body = mockPost.mock.calls.find(([url]) => url === "/api/pipeline-runs")?.[1];
      expect(body.drop_samples_without_files).toBe(true);
    });
  });

  it("asks again when the selection changes, rather than carrying the decision over", async () => {
    await toTheBlock();
    await takeTheDrop();

    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await waitFor(() => expect(screen.getByText("SLIDE-1")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("checkbox", { name: /Select sample SLIDE-1/i }));

    // A drop that was right for one selection is not a standing answer for the
    // next: a sample added later must not be dropped by a decision taken before
    // it existed. The block returns and has to be taken again.
    await waitFor(() => expect(lastPreflightBody().drop_samples_without_files).toBe(false));
  });
});
