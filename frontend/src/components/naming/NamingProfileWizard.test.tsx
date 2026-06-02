/**
 * Behavioral tests for the redesigned Naming Profile wizard.
 *
 * The wizard's contract lives in local/Naming Profiles/spec-wizard.md and
 * the redesign plan in the same directory. These tests assert the externally
 * visible behaviors described there; they do not assert internal component
 * structure beyond what end users can see.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { NamingProfileWizard } from "./NamingProfileWizard";

jest.mock("@/lib/api", () => ({
  api: {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
  },
}));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

const TEMPLATES = [
  {
    id: 7,
    name: "RNA-seq Template",
    custom_fields_schema_json: {
      fields: [
        { name: "Read", type: "number" },
        { name: "Lane", type: "number" },
        { name: "Requestor", type: "string" },
      ],
    },
  },
];

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url.startsWith("/api/experiments/templates")) {
      return Promise.resolve(TEMPLATES);
    }
    return Promise.resolve([]);
  });
  mockPost.mockReset();
  mockPost.mockResolvedValue({ id: 99, name: "Saved" });
});

function renderWizard(props: Partial<React.ComponentProps<typeof NamingProfileWizard>> = {}) {
  return render(
    <NamingProfileWizard
      onSave={props.onSave ?? jest.fn()}
      onCancel={props.onCancel ?? jest.fn()}
    />,
  );
}

// ---------------------------------------------------------------------------
// Layout / initial state
// ---------------------------------------------------------------------------

describe("initial render", () => {
  test("renders with no template selected and no segments", async () => {
    renderWizard();

    expect(await screen.findByLabelText(/profile name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/delimiter/i)).toBeInTheDocument();
    // Save is disabled when no segments exist (acceptance criteria).
    expect(screen.getByRole("button", { name: /save profile/i })).toBeDisabled();
  });

  test("shows system chips regardless of template selection", async () => {
    renderWizard();
    // System chips: Project, Experiment, Sample. They are always visible.
    expect(await screen.findByRole("button", { name: /project code chip/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /experiment code chip/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sample id chip/i })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Template picker
// ---------------------------------------------------------------------------

describe("template picker", () => {
  test("loads template fields into the available panel on template select", async () => {
    const user = userEvent.setup();
    renderWizard();

    const picker = await screen.findByLabelText(/experiment template/i);
    await waitFor(() =>
      expect(within(picker as HTMLSelectElement).getByText(/RNA-seq Template/i)).toBeInTheDocument(),
    );
    await user.selectOptions(picker, "7");

    expect(await screen.findByRole("button", { name: /Read/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Lane/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Requestor/ })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Add segment via system chip
// ---------------------------------------------------------------------------

describe("adding segments", () => {
  test("adding a system chip pre-fills its identifier and field type and enables save", async () => {
    const user = userEvent.setup();
    renderWizard();

    await user.click(await screen.findByRole("button", { name: /sample id chip/i }));

    // The new segment shows up in the segments list and Save becomes enabled.
    const segmentsList = await screen.findByTestId("segments-list");
    expect(within(segmentsList).getByText(/SampleID/i)).toBeInTheDocument();
    expect(within(segmentsList).getByDisplayValue("SMP")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save profile/i })).toBeEnabled();
  });
});

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

describe("client-side validation", () => {
  test("duplicate identifiers block save and surface an inline error", async () => {
    const user = userEvent.setup();
    renderWizard();

    // Add the Sample chip twice (the second time produces a duplicate
    // identifier because they share the SMP default).
    const sampleChip = await screen.findByRole("button", { name: /sample id chip/i });
    await user.click(sampleChip);
    await user.click(sampleChip);

    expect(await screen.findByText(/identifier .* is used by more than one/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save profile/i })).toBeDisabled();
  });

  test("YYYY-MM-DD plus delimiter '-' shows a warning but does not block save", async () => {
    const user = userEvent.setup();
    renderWizard();

    await user.selectOptions(screen.getByLabelText(/delimiter/i), "-");
    await user.click(await screen.findByRole("button", { name: /add date segment/i }));
    await user.selectOptions(screen.getByLabelText(/date format/i), "YYYY-MM-DD");

    expect(
      await screen.findByText(/shares its separator with the profile delimiter/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save profile/i })).toBeEnabled();
  });
});

// ---------------------------------------------------------------------------
// Test field
// ---------------------------------------------------------------------------

describe("test field", () => {
  test("calls backend test endpoint and renders parsed output", async () => {
    const user = userEvent.setup();

    mockPost.mockImplementation((url: string) => {
      if (url === "/api/naming-profiles/test") {
        return Promise.resolve([
          {
            filename: "SMP0042.fastq.gz",
            parsed: { SampleID: "0042" },
            unrecognized: [],
            warnings: [],
          },
        ]);
      }
      return Promise.resolve({ id: 99 });
    });

    renderWizard();
    await user.click(await screen.findByRole("button", { name: /sample id chip/i }));
    await user.type(screen.getByLabelText(/test against a real filename/i), "SMP0042.fastq.gz");
    await user.click(screen.getByRole("button", { name: /parse$/i }));

    const result = await screen.findByTestId("parse-result");
    expect(within(result).getByText("SampleID")).toBeInTheDocument();
    expect(within(result).getByText(/0042/)).toBeInTheDocument();
  });

  test("renders unrecognized tokens", async () => {
    const user = userEvent.setup();
    mockPost.mockResolvedValue([
      {
        filename: "garbage.txt",
        parsed: {},
        unrecognized: ["garbage"],
        warnings: [],
      },
    ]);

    renderWizard();
    await user.click(await screen.findByRole("button", { name: /sample id chip/i }));
    await user.type(screen.getByLabelText(/test against a real filename/i), "garbage.txt");
    await user.click(screen.getByRole("button", { name: /parse$/i }));

    const result = await screen.findByTestId("parse-result");
    expect(within(result).getByText(/unrecognized/i)).toBeInTheDocument();
    expect(within(result).getByText("garbage")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

describe("save", () => {
  test("save with no template skips the promotion prompt and posts a clean payload", async () => {
    const user = userEvent.setup();
    const onSave = jest.fn();
    renderWizard({ onSave });

    await user.type(screen.getByLabelText(/profile name/i), "Team A");
    await user.click(await screen.findByRole("button", { name: /sample id chip/i }));
    await user.click(screen.getByRole("button", { name: /save profile/i }));

    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    const [url, payload] = mockPost.mock.calls[mockPost.mock.calls.length - 1];
    expect(url).toBe("/api/naming-profiles");
    expect(payload.name).toBe("Team A");
    expect(payload.experiment_template_id).toBeNull();
    expect(payload.segments).toHaveLength(1);
    expect(payload.segments[0].identifier).toBe("SMP");
    expect(payload.segments[0].field_type).toBe("number");
    expect(payload.segments[0].is_system_chip).toBe(true);

    expect(onSave).toHaveBeenCalled();
  });

  test("backend save failure preserves in-progress state and surfaces error", async () => {
    const user = userEvent.setup();
    mockPost.mockImplementation((url: string) => {
      if (url === "/api/naming-profiles") {
        return Promise.reject(new Error("boom"));
      }
      return Promise.resolve({});
    });

    renderWizard();
    await user.type(screen.getByLabelText(/profile name/i), "Team A");
    await user.click(await screen.findByRole("button", { name: /sample id chip/i }));
    await user.click(screen.getByRole("button", { name: /save profile/i }));

    expect(await screen.findByText(/failed to save/i)).toBeInTheDocument();
    // Name input is still populated; the user keeps their work.
    expect(screen.getByLabelText(/profile name/i)).toHaveValue("Team A");
  });
});
