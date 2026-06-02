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

async function addSystemSampleSegment(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /sample id segment/i }));
}

async function addCustomSegment(
  user: ReturnType<typeof userEvent.setup>,
  name: string,
  type: "string" | "number" | "date" = "string",
) {
  await user.click(await screen.findByText(/Create new segment/i));
  await user.type(screen.getByLabelText(/new segment name/i), name);
  if (type !== "string") {
    await user.selectOptions(screen.getByLabelText(/new segment type/i), type);
  }
  await user.click(screen.getByRole("button", { name: /add segment$/i }));
}

async function selectTemplate(user: ReturnType<typeof userEvent.setup>, templateId: string) {
  const picker = await screen.findByLabelText(/experiment template/i);
  await waitFor(() =>
    expect(within(picker as HTMLSelectElement).getByText(/RNA-seq Template/i)).toBeInTheDocument(),
  );
  await user.selectOptions(picker, templateId);
}

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
    if (url.startsWith("/api/templates")) {
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
    expect(await screen.findByRole("button", { name: /project code segment/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /experiment code segment/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sample id segment/i })).toBeInTheDocument();
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

    await user.click(await screen.findByRole("button", { name: /sample id segment/i }));

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

describe("string segment hint", () => {
  test("shows italic 'hint' and an example using the delimiter and identifier", async () => {
    const user = userEvent.setup();
    renderWizard();

    // Create a string segment (custom field), then change its identifier
    // and watch the example update with both the delimiter and the new id.
    await user.click(await screen.findByText(/Create new segment/i));
    await user.type(screen.getByLabelText(/new segment name/i), "Requestor");
    await user.click(screen.getByRole("button", { name: /add segment$/i }));

    const segmentsList = await screen.findByTestId("segments-list");

    // The literal "hint" word is rendered as italic; the example after
    // " -> " uses the actual delimiter's inner separator.
    // Default delimiter is "_", default identifier from "Requestor" is "REQ".
    expect(within(segmentsList).getByText("hint")).toContainHTML("<i>hint</i>");
    expect(within(segmentsList).getByText(/-> REQ-text/)).toBeInTheDocument();

    // Flip the delimiter to "-" -- inner sep becomes "_".
    await user.selectOptions(screen.getByLabelText(/delimiter/i), "-");
    expect(within(segmentsList).getByText(/-> REQ_text/)).toBeInTheDocument();

    // Override the identifier to "OPR" -- example follows.
    const identInput = within(segmentsList).getByLabelText("identifier-0");
    await user.clear(identInput);
    await user.type(identInput, "OPR");
    expect(within(segmentsList).getByText(/-> OPR_text/)).toBeInTheDocument();
  });
});

describe("system segments", () => {
  test("system segments do not show a padding input", async () => {
    const user = userEvent.setup();
    renderWizard();

    await user.click(await screen.findByRole("button", { name: /sample id segment/i }));
    const segmentsList = await screen.findByTestId("segments-list");

    // Padding is a write-time concept and the parser is lenient. The
    // system segments are auto-generated by bioAF, so we don't ask the
    // user to specify padding.
    expect(within(segmentsList).queryByLabelText(/padding-/)).not.toBeInTheDocument();
  });

  test("user-added number segments still show a padding input", async () => {
    const user = userEvent.setup();
    renderWizard();

    await user.click(await screen.findByText(/Create new segment/i));
    await user.type(screen.getByLabelText(/new segment name/i), "Read");
    await user.selectOptions(screen.getByLabelText(/new segment type/i), "number");
    await user.click(screen.getByRole("button", { name: /add segment$/i }));

    const segmentsList = await screen.findByTestId("segments-list");
    expect(within(segmentsList).getByLabelText("padding-0")).toBeInTheDocument();
  });
});

describe("client-side validation", () => {
  test("duplicate identifiers block save and surface an inline error", async () => {
    const user = userEvent.setup();
    renderWizard();

    // Add the Sample chip twice (the second time produces a duplicate
    // identifier because they share the SMP default).
    const sampleChip = await screen.findByRole("button", { name: /sample id segment/i });
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
    await user.click(await screen.findByRole("button", { name: /sample id segment/i }));
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
    await user.click(await screen.findByRole("button", { name: /sample id segment/i }));
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
    await user.click(await screen.findByRole("button", { name: /sample id segment/i }));
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

  test("save with no template and custom segments skips the promotion modal", async () => {
    const user = userEvent.setup();
    const onSave = jest.fn();
    renderWizard({ onSave });

    // No template selected, but the user adds an ad-hoc segment.
    await addCustomSegment(user, "Requestor", "string");
    await user.type(screen.getByLabelText(/profile name/i), "Team A");
    await user.click(screen.getByRole("button", { name: /save profile/i }));

    // No modal should appear; profile is saved straight through.
    expect(screen.queryByRole("dialog", { name: /add new segments to template/i })).not.toBeInTheDocument();
    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    expect(onSave).toHaveBeenCalled();
  });

  test("save with template and only template / system segments skips the promotion modal", async () => {
    const user = userEvent.setup();
    const onSave = jest.fn();
    renderWizard({ onSave });

    await selectTemplate(user, "7");
    // Pick a template field and a system chip. No ad-hoc fields.
    await user.click(await screen.findByRole("button", { name: /Read/ }));
    await addSystemSampleSegment(user);
    await user.type(screen.getByLabelText(/profile name/i), "Team A");
    await user.click(screen.getByRole("button", { name: /save profile/i }));

    expect(screen.queryByRole("dialog", { name: /add new segments to template/i })).not.toBeInTheDocument();
    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    expect(onSave).toHaveBeenCalled();
  });

  test("save with template and custom segments opens the promotion modal", async () => {
    const user = userEvent.setup();
    renderWizard();

    await selectTemplate(user, "7");
    await addCustomSegment(user, "Operator", "string");
    await addCustomSegment(user, "Rescan", "number");
    await user.type(screen.getByLabelText(/profile name/i), "Team A");
    await user.click(screen.getByRole("button", { name: /save profile/i }));

    const dialog = await screen.findByRole("dialog", { name: /add new segments to template/i });
    // Each new field gets one row in the table.
    expect(within(dialog).getByText("Operator")).toBeInTheDocument();
    expect(within(dialog).getByText("Rescan")).toBeInTheDocument();
    // Nothing has been posted yet.
    expect(mockPost).not.toHaveBeenCalled();
  });

  test("modal confirm PATCHes template with checked rows then POSTs profile", async () => {
    const user = userEvent.setup();
    const onSave = jest.fn();
    const mockPatch = api.patch as jest.Mock;
    mockPatch.mockResolvedValue({});
    renderWizard({ onSave });

    await selectTemplate(user, "7");
    await addCustomSegment(user, "Operator", "string");
    await addCustomSegment(user, "Rescan", "number");
    await user.type(screen.getByLabelText(/profile name/i), "Team A");
    await user.click(screen.getByRole("button", { name: /save profile/i }));

    const dialog = await screen.findByRole("dialog", { name: /add new segments to template/i });
    // Mark Operator as required; leave Rescan unrequired.
    await user.click(within(dialog).getByLabelText("required-Operator"));
    // Uncheck Rescan's 'Add to Template' so it is NOT promoted.
    await user.click(within(dialog).getByLabelText("add-to-template-Rescan"));
    await user.click(within(dialog).getByRole("button", { name: /save profile$/i }));

    await waitFor(() => expect(mockPatch).toHaveBeenCalled());
    const [patchUrl, patchBody] = mockPatch.mock.calls[0];
    expect(patchUrl).toBe("/api/templates/7");
    expect(patchBody.custom_fields_schema_json.fields).toEqual(
      expect.arrayContaining([
        { name: "Read", type: "number" }, // existing template field preserved
        { name: "Lane", type: "number" },
        { name: "Requestor", type: "string" },
        { name: "Operator", type: "string", required: true },
      ]),
    );
    // Rescan was unchecked; it must not be in the template payload.
    expect(
      patchBody.custom_fields_schema_json.fields.some(
        (f: { name: string }) => f.name === "Rescan",
      ),
    ).toBe(false);

    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/naming-profiles", expect.anything()));
    expect(onSave).toHaveBeenCalled();
  });

  test("modal cancel closes without saving anything", async () => {
    const user = userEvent.setup();
    const onSave = jest.fn();
    const mockPatch = api.patch as jest.Mock;
    mockPatch.mockReset();
    renderWizard({ onSave });

    await selectTemplate(user, "7");
    await addCustomSegment(user, "Operator", "string");
    await user.type(screen.getByLabelText(/profile name/i), "Team A");
    await user.click(screen.getByRole("button", { name: /save profile/i }));

    const dialog = await screen.findByRole("dialog", { name: /add new segments to template/i });
    await user.click(within(dialog).getByRole("button", { name: /^cancel$/i }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockPatch).not.toHaveBeenCalled();
    expect(mockPost).not.toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
  });

  test("edit mode prefills state from the profile and PUTs on save", async () => {
    const user = userEvent.setup();
    const onSave = jest.fn();
    const mockPut = api.put as jest.Mock | undefined;
    const putMock = mockPut ?? jest.fn();
    (api as unknown as { put: jest.Mock }).put = putMock;
    putMock.mockReset();
    putMock.mockResolvedValue({});

    const existing = {
      id: 99,
      organization_id: 1,
      name: "Existing profile",
      description: "Old description",
      delimiter: "_" as const,
      strip_extension: true,
      segments: [
        {
          position: 0,
          identifier: "SMP",
          field_name: "SampleID",
          field_type: "number" as const,
          padding: 2,
          date_format: null,
          is_system_chip: true,
        },
      ],
      experiment_template_id: null,
      status: "active" as const,
      created_by: 1,
      created_at: "2026-06-02T00:00:00Z",
      updated_at: "2026-06-02T00:00:00Z",
    };

    render(
      <NamingProfileWizard onSave={onSave} onCancel={jest.fn()} profile={existing} />,
    );

    // Existing values are reflected in the form and the segment is in the list.
    const nameInput = await screen.findByLabelText(/profile name/i);
    expect(nameInput).toHaveValue("Existing profile");
    const segmentsList = await screen.findByTestId("segments-list");
    expect(within(segmentsList).getByText(/SampleID/)).toBeInTheDocument();

    // Rename and save.
    await user.clear(nameInput);
    await user.type(nameInput, "Renamed");
    await user.click(screen.getByRole("button", { name: /save profile/i }));

    await waitFor(() => expect(putMock).toHaveBeenCalled());
    const [url, body] = putMock.mock.calls[0];
    expect(url).toBe("/api/naming-profiles/99");
    expect(body.name).toBe("Renamed");
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
    await user.click(await screen.findByRole("button", { name: /sample id segment/i }));
    await user.click(screen.getByRole("button", { name: /save profile/i }));

    expect(await screen.findByText(/failed to save/i)).toBeInTheDocument();
    // Name input is still populated; the user keeps their work.
    expect(screen.getByLabelText(/profile name/i)).toHaveValue("Team A");
  });
});
