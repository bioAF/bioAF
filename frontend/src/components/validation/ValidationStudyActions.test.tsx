import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ValidationStudyActions } from "./ValidationStudyActions";

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, roleName: "admin", loading: false, permissions: new Set() }),
}));

jest.mock("@/lib/api", () => ({ api: { post: jest.fn() } }));
import { api } from "@/lib/api";
const mockPost = api.post as jest.Mock;

beforeEach(() => {
  mockPost.mockReset();
});

// The server's own words. A plan naming a pipeline that cannot read the data the study is scoped to
// is refused at approve time, and the refusal is one plain sentence naming both sides.
const CONFLICT =
  "nf-core/atacseq does not consume Bisulfite-Seq data, and the accession this study was scoped to " +
  "is deposited as Bisulfite-Seq. Running it would spend the compute and answer confidently about " +
  "the wrong thing. nf-core/methylseq is the pipeline for Bisulfite-Seq data.";

test("a refused approval says which pipeline and which data, on screen", async () => {
  mockPost.mockRejectedValue(new Error(CONFLICT));
  const onChanged = jest.fn();
  render(<ValidationStudyActions study={{ id: 7, state: "plan_ready" }} onChanged={onChanged} />);

  await userEvent.click(screen.getByRole("button", { name: /^approve/i }));
  await userEvent.click(screen.getByRole("button", { name: /approve and run/i }));

  // The gate now always sends the route explicitly (it used to send no body and lean on the server
  // default). A caller relying on a server default is one flip away from silently spending hours of
  // compute, so the UI states its choice every time.
  await waitFor(() =>
    expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/7/approve", { route: "deposit" }),
  );
  expect(await screen.findByText(/nf-core\/atacseq/)).toBeInTheDocument();
  expect(screen.getByText(/Bisulfite-Seq/)).toBeInTheDocument();
  expect(screen.getByText(/nf-core\/methylseq/)).toBeInTheDocument();
  // The study has not moved: a refusal is not a state change.
  expect(onChanged).not.toHaveBeenCalled();
});

test("an approval the server accepts hands back the updated study", async () => {
  mockPost.mockResolvedValue({ id: 7, state: "acquiring_data" });
  const onChanged = jest.fn();
  render(<ValidationStudyActions study={{ id: 7, state: "plan_ready" }} onChanged={onChanged} />);

  await userEvent.click(screen.getByRole("button", { name: /^approve/i }));
  await userEvent.click(screen.getByRole("button", { name: /approve and run/i }));

  await waitFor(() => expect(onChanged).toHaveBeenCalledWith({ id: 7, state: "acquiring_data" }));
});

// A study back at the approval gate after a retry is not the same decision as a first approval:
// its data was already downloaded once and deleted, so approving pays for the download again.

test("warns that approving re-downloads when the study is back from a retry", () => {
  render(
    <ValidationStudyActions
      study={{ id: 7, state: "plan_ready", evidence: { awaiting_refetch_approval: true } }}
      onChanged={jest.fn()}
    />,
  );
  expect(screen.getByText(/download/i)).toBeInTheDocument();
  expect(screen.getByText(/again/i)).toBeInTheDocument();
});

test("says nothing about re-downloading on a study that never ran", () => {
  render(<ValidationStudyActions study={{ id: 7, state: "plan_ready" }} onChanged={jest.fn()} />);
  expect(screen.queryByText(/download the data again/i)).not.toBeInTheDocument();
});

test("does not offer Approve while the plan might run the wrong tool", () => {
  // Approve used to be enabled here, and clicking it returned a 400 the scientist could do nothing
  // about. The conflict notice carries the two ways out; this control waits for them.
  render(
    <ValidationStudyActions
      study={{ id: 7, state: "plan_ready", plan: { deposit_conflict: { message: "x" } } }}
      onChanged={jest.fn()}
    />,
  );
  expect(screen.queryByRole("button", { name: /approve plan/i })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /decline/i })).toBeInTheDocument();
});

test("offers Approve again once the conflict has been answered", () => {
  render(
    <ValidationStudyActions
      study={{
        id: 7,
        state: "plan_ready",
        plan: { deposit_conflict: { message: "x", override: { reason: "mislabelled" } } },
      }}
      onChanged={jest.fn()}
    />,
  );
  expect(screen.getByRole("button", { name: /approve plan/i })).toBeInTheDocument();
});

// ---- the route modal: chosen at the C1 gate, GEO by default ----

function renderActions(study: { id: number; state: string }) {
  render(<ValidationStudyActions study={study} onChanged={jest.fn()} />);
}

async function openModal() {
  await userEvent.click(screen.getByRole("button", { name: /^approve plan/i }));
}

describe("the route modal", () => {
  it("offers all three routes", async () => {
    renderActions({ id: 7, state: "plan_ready" });
    await openModal();
    expect(screen.getByLabelText(/deposited data/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/raw reads/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/both/i)).toBeInTheDocument();
  });

  it("defaults to the deposited-data route", async () => {
    renderActions({ id: 7, state: "plan_ready" });
    await openModal();
    expect(screen.getByLabelText(/deposited data/i)).toBeChecked();
    expect(screen.getByLabelText(/raw reads/i)).not.toBeChecked();
  });

  it("states what each route actually tests", async () => {
    renderActions({ id: 7, state: "plan_ready" });
    await openModal();
    expect(screen.getByText(/validates the computational findings/i)).toBeInTheDocument();
    expect(screen.getByText(/validates the pre-processing and sample quality/i)).toBeInTheDocument();
  });

  it("states the cost on both sides, because that is the basis of the choice", async () => {
    renderActions({ id: 7, state: "plan_ready" });
    await openModal();
    expect(screen.getByText(/takes minutes/i)).toBeInTheDocument();
    expect(screen.getByText(/takes hours/i)).toBeInTheDocument();
  });

  it("sends the deposit route when the default is accepted", async () => {
    renderActions({ id: 7, state: "plan_ready" });
    await openModal();
    await userEvent.click(screen.getByRole("button", { name: /approve and run/i }));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/7/approve", { route: "deposit" }),
    );
  });

  it("sends the raw-reads route when chosen", async () => {
    renderActions({ id: 7, state: "plan_ready" });
    await openModal();
    await userEvent.click(screen.getByLabelText(/raw reads/i));
    await userEvent.click(screen.getByRole("button", { name: /approve and run/i }));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/7/approve", { route: "pipeline" }),
    );
  });

  it("sends both when chosen", async () => {
    renderActions({ id: 7, state: "plan_ready" });
    await openModal();
    await userEvent.click(screen.getByLabelText(/both/i));
    await userEvent.click(screen.getByRole("button", { name: /approve and run/i }));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/7/approve", { route: "both" }),
    );
  });

  it("always sends the route explicitly, so no caller relies on a server default", async () => {
    renderActions({ id: 7, state: "plan_ready" });
    await openModal();
    await userEvent.click(screen.getByRole("button", { name: /approve and run/i }));
    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    expect(mockPost.mock.calls[0][1]).not.toBeUndefined();
  });

  it("warns that the raw route is the one that spends real compute", async () => {
    renderActions({ id: 7, state: "plan_ready" });
    await openModal();
    await userEvent.click(screen.getByLabelText(/raw reads/i));
    expect(screen.getByText(/spends compute on your cloud account/i)).toBeInTheDocument();
  });

  it("does not warn about cloud spend for the deposited-data route", async () => {
    renderActions({ id: 7, state: "plan_ready" });
    await openModal();
    expect(screen.queryByText(/spends compute on your cloud account/i)).not.toBeInTheDocument();
  });

  it("says the deposited route cannot test the processing", async () => {
    renderActions({ id: 7, state: "plan_ready" });
    await openModal();
    expect(screen.getByText(/cannot detect a processing error/i)).toBeInTheDocument();
  });
});

/**
 * plan_7 step 15: the route modal must show what is actually available.
 *
 * `7d36acad` shipped the modal offering all three routes blind, so a person could choose the
 * deposited-data route on a paper with no deposited matrix and only discover it after approving.
 *
 * An UNKNOWN capability is OFFERED, not hidden: treating it as NO would hide a workable route
 * behind a GEO timeout, and treating it as YES would promise a route that may not exist.
 */

const yes = { value: "yes" as const, evidence: null, failure_reason: null };
const no = { value: "no" as const, evidence: null, failure_reason: null };

const CAPS = {
  paper_readable: yes,
  deposit_exists: yes,
  raw_data: yes,
  preprocessed_data: yes,
  sample_metadata: yes,
  code_artifact: no,
  code_repository: no,
  code_sources: [] as { kind: string; url: string | null; identifier: string | null }[],
  deposits: [] as unknown[],
};

async function openApprove() {
  await userEvent.click(screen.getByRole("button", { name: /^approve/i }));
}

test("a route with no data behind it says so instead of looking equal to the others", async () => {
  render(
    <ValidationStudyActions
      study={{
        id: 7,
        state: "plan_ready",
        evidence: { capabilities: { ...CAPS, preprocessed_data: no } },
      }}
      onChanged={jest.fn()}
    />,
  );
  await openApprove();
  expect(screen.getByText(/no pre-processed data was found/i)).toBeInTheDocument();
});

test("an unknown capability is still offered, with the uncertainty stated", async () => {
  render(
    <ValidationStudyActions
      study={{
        id: 7,
        state: "plan_ready",
        evidence: {
          capabilities: {
            ...CAPS,
            preprocessed_data: {
              value: "unknown" as const,
              evidence: null,
              failure_reason: "bioAF could not reach GEO to check for a deposited matrix",
            },
          },
        },
      }}
      onChanged={jest.fn()}
    />,
  );
  await openApprove();
  expect(screen.getByText(/could not reach GEO/i)).toBeInTheDocument();
  // Offered, not hidden: the radio is still selectable.
  expect(screen.getByRole("radio", { name: /deposited data/i })).toBeEnabled();
});

test("it states which method the run will try first, and why", async () => {
  render(
    <ValidationStudyActions
      study={{
        id: 7,
        state: "plan_ready",
        evidence: {
          capabilities: {
            ...CAPS,
            code_repository: yes,
            code_sources: [{ kind: "github", url: "https://github.com/lab/paper", identifier: null }],
          },
        },
      }}
      onChanged={jest.fn()}
    />,
  );
  await openApprove();
  expect(screen.getByText(/authors.*code will be attempted first/i)).toBeInTheDocument();
  expect(screen.getByText(/github\.com\/lab\/paper/)).toBeInTheDocument();
});

test("with no published code it says an analysis will be generated from the described methods", async () => {
  render(
    <ValidationStudyActions
      study={{ id: 7, state: "plan_ready", evidence: { capabilities: CAPS } }}
      onChanged={jest.fn()}
    />,
  );
  await openApprove();
  expect(screen.getByText(/generated from the methods the paper describes/i)).toBeInTheDocument();
});

test("the modal is unchanged when discovery never ran", async () => {
  render(<ValidationStudyActions study={{ id: 7, state: "plan_ready" }} onChanged={jest.fn()} />);
  await openApprove();
  expect(screen.queryByText(/no pre-processed data was found/i)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /approve and run/i })).toBeInTheDocument();
});

// change_7.2 section 3: a running acquisition can be stopped, and a stopped study can be picked back
// up, from the product. `/decline` needs `plan_ready` and `/classify` needs `comparing`, so a study
// looping in `acquiring_processed` could be stopped by nothing at all: studies 29 and 33 were parked
// in `error` by a direct database write.

test("a study acquiring a deposit offers a way to stop it", () => {
  render(<ValidationStudyActions study={{ id: 7, state: "acquiring_processed" }} onChanged={jest.fn()} />);
  expect(screen.getByRole("button", { name: /stop acquisition/i })).toBeInTheDocument();
});

test("a study inspecting a deposit offers it too", () => {
  render(<ValidationStudyActions study={{ id: 7, state: "inspecting_deposit" }} onChanged={jest.fn()} />);
  expect(screen.getByRole("button", { name: /stop acquisition/i })).toBeInTheDocument();
});

test("stopping sends the reason so the record says why", async () => {
  mockPost.mockResolvedValue({ id: 7, state: "classified" });
  render(<ValidationStudyActions study={{ id: 7, state: "acquiring_data" }} onChanged={jest.fn()} />);

  await userEvent.type(screen.getByLabelText(/why are you stopping/i), "wrong accession");
  await userEvent.click(screen.getByRole("button", { name: /stop acquisition/i }));

  await waitFor(() =>
    expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/7/cancel-acquisition", {
      reason: "wrong accession",
    }),
  );
});

test("a study waiting for a person to choose says what would resolve it", () => {
  render(
    <ValidationStudyActions
      study={{
        id: 7,
        state: "acquiring_processed",
        evidence: {
          awaiting_choice: {
            reason: "a person must select a file at the gate",
            action: "choose a deposited file at the gate, or cancel the acquisition",
          },
        },
      }}
      onChanged={jest.fn()}
    />,
  );
  expect(screen.getByText(/choose a deposited file at the gate/i)).toBeInTheDocument();
});

test("a study whose earlier run may still be going asks before starting another", () => {
  render(
    <ValidationStudyActions
      study={{
        id: 7,
        state: "acquiring_data",
        evidence: {
          awaiting_adoption: {
            operation: "data_acquisition",
            action: "resume the run that is already going, or cancel it and start a new one",
          },
        },
      }}
      onChanged={jest.fn()}
    />,
  );
  expect(screen.getByText(/resume the run that is already going/i)).toBeInTheDocument();
});

test("a study that reached an outcome can be resumed at the gate", async () => {
  mockPost.mockResolvedValue({ id: 7, state: "plan_ready" });
  render(<ValidationStudyActions study={{ id: 7, state: "classified" }} onChanged={jest.fn()} />);

  // change_7.3 section 10 item 10 (flagged test change): "Resume at the gate" is "Review and resume".
  await userEvent.click(screen.getByRole("button", { name: /review and resume/i }));

  await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/api/validation-studies/7/resume", {}));
});

test("a study parked in error can be resumed the same way", () => {
  render(<ValidationStudyActions study={{ id: 7, state: "error" }} onChanged={jest.fn()} />);
  // change_7.3 section 10 item 10 (flagged test change): the control's new name.
  expect(screen.getByRole("button", { name: /review and resume/i })).toBeInTheDocument();
});

test("resuming says it returns to the gate rather than restarting a run", () => {
  render(<ValidationStudyActions study={{ id: 7, state: "classified" }} onChanged={jest.fn()} />);
  expect(screen.getByText(/decide again/i)).toBeInTheDocument();
});


describe("change_7.3 section 10 item 10: resuming says what would unblock this study", () => {
  const requirements = [
    "bioAF cannot yet acquire data from EGA, so credentials alone will not make this study runnable.",
    "Approving again retries the attachment download.",
  ];

  test("the helper text is generated from this study's limitations", () => {
    render(
      <ValidationStudyActions
        study={{ id: 7, state: "classified", resume: { label: "Review and resume", requirements } }}
        onChanged={jest.fn()}
      />,
    );
    expect(screen.getByText(/credentials alone will not make this study runnable/)).toBeInTheDocument();
    expect(screen.getByText(/retries the attachment download/)).toBeInTheDocument();
  });

  test("it never implies credentials alone would do for an archive bioAF cannot read", () => {
    render(
      <ValidationStudyActions
        study={{ id: 7, state: "classified", resume: { label: "Review and resume", requirements } }}
        onChanged={jest.fn()}
      />,
    );
    expect(screen.queryByText(/Credentials, a corrected accession/)).not.toBeInTheDocument();
  });
});

// Study 37: a study whose route was chosen at the button is read by bioAF on its own. "Read paper"
// raced that read, lost, and came back as an error about a worker nobody could see.
describe("a study that reads itself", () => {
  const since = "2026-09-11T15:45:04+00:00";

  test("offers no Read paper click", () => {
    render(
      <ValidationStudyActions
        study={{ id: 37, state: "requested", intended_route: "deposit", activity: { advancing: true, working: false, since: null } }}
        onChanged={jest.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /read paper/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  test("says it is queued before a worker takes it", () => {
    render(
      <ValidationStudyActions
        study={{ id: 37, state: "requested", intended_route: "deposit", activity: { advancing: true, working: false, since: null } }}
        onChanged={jest.fn()}
      />,
    );
    expect(screen.getByTestId("study-activity")).toHaveTextContent(/starts reading the paper on its own/i);
  });

  test("says the read is under way, and since when", () => {
    render(
      <ValidationStudyActions
        study={{ id: 37, state: "requested", intended_route: "deposit", activity: { advancing: true, working: true, since } }}
        onChanged={jest.fn()}
      />,
    );
    const status = screen.getByTestId("study-activity");
    expect(status).toHaveTextContent(/reading the paper/i);
    expect(status).toHaveTextContent(new RegExp(`Started ${new Date(since).toLocaleTimeString()}`));
  });

  test("a study with no route still waits for Read paper, since nothing else will read it", () => {
    render(<ValidationStudyActions study={{ id: 7, state: "requested" }} onChanged={jest.fn()} />);
    expect(screen.getByRole("button", { name: /read paper/i })).toBeInTheDocument();
    expect(screen.queryByTestId("study-activity")).not.toBeInTheDocument();
  });
});
