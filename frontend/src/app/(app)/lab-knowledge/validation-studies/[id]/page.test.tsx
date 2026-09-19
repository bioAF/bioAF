/**
 * change_7.3 sections 10 and 11: the study page renders the report projection the API returns.
 *
 * The study is Groff on the current build with the attachment bundle failing: nothing executed, the
 * raw reads sit under controlled access in an archive bioAF cannot read, and no claim was compared.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@/testing/renderWithProviders";

import contract from "@/components/validation/__fixtures__/reportContract.json";
import ValidationStudyPage from "./page";

// One router for the whole test: the page's load effect depends on it, and a new object on every
// render re-runs that effect and fetches again, which would read as polling.
const mockRouter = { push: jest.fn(), back: jest.fn() };
jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "34" }),
  useRouter: () => mockRouter,
  usePathname: () => "/lab-knowledge/validation-studies/34",
}));
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, roleName: "admin", loading: false }),
}));
jest.mock("@/hooks/useBetaFeatures", () => ({
  useBetaFeatures: () => ({ flags: { lit_validation: true }, loading: false }),
}));
jest.mock("@/lib/auth", () => ({
  isAuthenticated: () => true,
  getCurrentUser: () => ({ id: 1, role_name: "admin" }),
}));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));

import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

function study() {
  return {
    id: 34,
    state: "classified",
    title: "RNA-seq as a tool for evaluating human embryo competence",
    classification: "access_restricted",
    confidence: null,
    source_doi: "10.1101/gr.252981.119",
    failure_reason: "recorded reason",
    plan: { pipeline_key: "nf-core/rnaseq", blockers: [], ai_decisions: [] },
    evidence: { supplements: [{ label: "x" }], completion: { limitations: [] }, capabilities: {} },
    issues: [],
    report_summary: contract.groff_failed,
  };
}

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockResolvedValue(study());
});

test("the headline says reproduction was not attempted", async () => {
  render(<ValidationStudyPage />);
  await waitFor(() => expect(screen.getByText("Reproduction not attempted")).toBeInTheDocument());
});

test("the comparison section says comparisons were not performed, and why (item 9)", async () => {
  render(<ValidationStudyPage />);
  await waitFor(() => expect(screen.getByText("Comparisons not performed")).toBeInTheDocument());
  expect(screen.getByText(/reproduction was not attempted, so no claim was compared/i)).toBeInTheDocument();
  expect(screen.queryByText("What we expected, and what we saw")).not.toBeInTheDocument();
});

test("the evidence table says there are no execution results (item 8)", async () => {
  render(<ValidationStudyPage />);
  await waitFor(() =>
    expect(screen.getByText("No execution results: reproduction was not attempted.")).toBeInTheDocument(),
  );
});

test("reconciliation says on what basis it ran (item 13)", async () => {
  render(<ValidationStudyPage />);
  await waitFor(() => expect(screen.getByText(/Reconciled against the paper's own passages/)).toBeInTheDocument());
});

test("the resume control is Review and resume, with this study's requirements (item 10)", async () => {
  render(<ValidationStudyPage />);
  await waitFor(() => expect(screen.getByRole("button", { name: /review and resume/i })).toBeInTheDocument());
  expect(screen.getByText(/credentials alone will not make this study runnable/)).toBeInTheDocument();
});

test("the checklist separates deposited from available to bioAF (item 11)", async () => {
  render(<ValidationStudyPage />);
  await waitFor(() => expect(screen.getByText("Raw sample data available to bioAF")).toBeInTheDocument());
});

test("the attachments show one failure notice, not one per row (item 4)", async () => {
  render(<ValidationStudyPage />);
  await waitFor(() => expect(screen.getAllByTestId("retrieval-failure")).toHaveLength(1));
});

// Study 37: the page loaded once in `requested` and never refreshed, so it still offered "Read paper"
// while the driver was already reading. The server says whether bioAF moves the study on by itself,
// and the page keeps itself current for exactly that long.
function readingItself() {
  return {
    id: 37,
    state: "requested",
    title: "A paper bioAF is reading",
    intended_route: "deposit",
    evidence: null,
    issues: [],
    report_summary: null,
    activity: { advancing: true, working: true, since: "2026-09-11T15:45:04+00:00" },
  };
}

test("a study bioAF is reading shows the read under way and offers no Read paper", async () => {
  mockGet.mockResolvedValue(readingItself());
  render(<ValidationStudyPage />);
  await waitFor(() => expect(screen.getByText(/reading the paper/i)).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: /read paper/i })).not.toBeInTheDocument();
});

describe("keeping the page current", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  async function fetchesOver(ms: number) {
    const before = mockGet.mock.calls.length;
    await act(async () => {
      jest.advanceTimersByTime(ms);
    });
    return mockGet.mock.calls.length - before;
  }

  test("polls while bioAF moves the study on, in a state the old list left out", async () => {
    mockGet.mockResolvedValue({ ...readingItself(), state: "inspecting_deposit" });
    render(<ValidationStudyPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "A paper bioAF is reading" })).toBeInTheDocument());
    expect(await fetchesOver(10000)).toBe(2);
  });

  test("does not poll a study that waits for a person", async () => {
    mockGet.mockResolvedValue({ ...study(), activity: { advancing: false, working: false, since: null } });
    render(<ValidationStudyPage />);
    await waitFor(() => expect(screen.getByText("Reproduction not attempted")).toBeInTheDocument());
    expect(await fetchesOver(15000)).toBe(0);
  });
});

test("blockers read from the prose are marked provisional (section 6)", async () => {
  mockGet.mockResolvedValue({
    ...study(),
    plan: { pipeline_key: "nf-core/rnaseq", blockers: ["Sample IDs are not enumerated in the text"], ai_decisions: [] },
    report_summary: {
      ...contract.groff_failed,
      blockers: [{ text: "Sample IDs are not enumerated in the text", kind: "sample_assignment", basis: "paper_text", provisional: true }],
    },
  });
  render(<ValidationStudyPage />);
  await waitFor(() => expect(screen.getByText("Sample IDs are not enumerated in the text")).toBeInTheDocument());
  expect(screen.getByTestId("blockers-provisional")).toHaveTextContent(/not checked against the attachments/);
});

// change_7.5 stage 2: the resources the paper names, and what this run checks, from the projection.
test("the page shows the paper's resources and each claim's checks", async () => {
  mockGet.mockResolvedValue({ ...study(), state: "plan_ready", report_summary: contract.stage2_selection });
  render(<ValidationStudyPage />);
  await waitFor(() => expect(screen.getByText("PXD099001")).toBeInTheDocument());
  expect(screen.getByText("Resources the paper names")).toBeInTheDocument();
  expect(screen.getByText("What this run checks")).toBeInTheDocument();
  expect(screen.getByTestId("claim-selection-current")).toBeInTheDocument();
});

test("the Validation Scorecard leads the report, above the outcome (plan_8 section 6)", async () => {
  mockGet.mockResolvedValue({ ...study(), report_summary: contract.scorecard_scored });
  render(<ValidationStudyPage />);
  // plan_8_4 section 7: the evidence score holds the name "Validation Scorecard" and leads; the v2
  // card follows it, named for what it measures. Both are above the outcome.
  const scorecard = await screen.findByRole("heading", { name: "Validation Scorecard" });
  const findings = screen.getByRole("heading", { name: "Findings Scorecard" });
  const outcome = screen.getByRole("heading", { name: "Outcome" });
  expect(scorecard.compareDocumentPosition(findings) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(scorecard.compareDocumentPosition(outcome) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getByTestId("scorecard-score")).toHaveTextContent("67 / 100");
  expect(screen.getByTestId("scorecard-scope")).toHaveTextContent("5 / 5 assessed");
});

test("a report projected before the scorecard existed renders no card and no error", async () => {
  const {
    scorecard: _omitted,
    evidence_score: _also,
    ...older
  } = contract.groff_failed as Record<string, unknown>;
  mockGet.mockResolvedValue({ ...study(), report_summary: older });
  render(<ValidationStudyPage />);
  await screen.findByText("Reproduction not attempted");
  expect(screen.queryByRole("heading", { name: "Validation Scorecard" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Findings Scorecard" })).not.toBeInTheDocument();
});

test("a historical report with no evidence score keeps its own card and its own name", async () => {
  // As the backend projects one: no evidence score, and the v2 card under the title it was stored
  // with, because nothing renamed it.
  const { evidence_score: _omitted, ...historical } = contract.scorecard_scored as Record<string, unknown>;
  const card = historical.scorecard as Record<string, unknown>;
  mockGet.mockResolvedValue({
    ...study(),
    report_summary: { ...historical, scorecard: { ...card, title: "Validation Scorecard" } },
  });
  render(<ValidationStudyPage />);
  expect(await screen.findByRole("heading", { name: "Validation Scorecard" })).toBeInTheDocument();
  expect(screen.queryByTestId("evidence-score-headline")).not.toBeInTheDocument();
});

// plan_8_1 sections 1.3 and 1.4: a read that failed is never shown as a fact about the paper.
describe("a study whose read failed", () => {
  function legacy42() {
    return {
      ...study(),
      id: 42,
      state: "classified",
      classification: "missing_data",
      plan: {
        pipeline_key: null,
        blockers: ["insufficient method detail to identify an assay", "no data accession found in the paper"],
        ai_decisions: [],
      },
      report_summary: contract.failed_read_legacy,
    };
  }

  test("lists the bioAF limitation and withholds each absence blocker as not established", async () => {
    mockGet.mockResolvedValue(legacy42());
    render(<ValidationStudyPage />);
    await waitFor(() =>
      expect(screen.getByText(/^bioAF could not read the paper: the model's answer was cut off/)).toBeInTheDocument(),
    );
    const withheld = screen.getAllByTestId("blocker-withheld");
    expect(withheld.length).toBe(contract.failed_read_legacy.blockers.length - 1);
    for (const row of withheld) {
      expect(row).toHaveTextContent(/^Not established: the paper was not read\./);
    }
    expect(screen.queryByTestId("blockers-provisional")).not.toBeInTheDocument();
  });

  test("says the classification came from a failed read", async () => {
    mockGet.mockResolvedValue(legacy42());
    render(<ValidationStudyPage />);
    await waitFor(() =>
      expect(screen.getByText(contract.failed_read_legacy.read_failure.classification_note as string)).toBeInTheDocument(),
    );
  });

  test("the scorecard names the bioAF limitation and the next action", async () => {
    mockGet.mockResolvedValue(legacy42());
    render(<ValidationStudyPage />);
    await waitFor(() => expect(screen.getByText(/This is a bioAF limitation; read the paper again\./)).toBeInTheDocument());
  });

  test("Retry says it reads the paper again", async () => {
    mockGet.mockResolvedValue({
      ...study(),
      state: "error",
      classification: null,
      failure_reason: contract.failed_read.blockers[0].text,
      report_summary: contract.failed_read,
    });
    render(<ValidationStudyPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument());
    expect(screen.getByText(/Retrying reads the paper again\./)).toBeInTheDocument();
    expect(screen.queryByText(/data that was already downloaded is reused/)).not.toBeInTheDocument();
  });
});

// plan_8_2 section 2.1 and decision 5: the recovery control appears only when a recovery would change
// something, beneath the scorecard.
describe("the on-request recovery", () => {
  test("is offered when some checks were made under rules bioAF has since replaced", async () => {
    mockGet.mockResolvedValue({
      ...study(),
      report_summary: { ...contract.groff_failed, recovery: { available: true, affected_count: 3, last: null } },
    });
    render(<ValidationStudyPage />);
    expect(await screen.findByTestId("recovery-notice")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review the re-evaluation" })).toBeInTheDocument();
  });

  test("is not offered when nothing would change", async () => {
    mockGet.mockResolvedValue({
      ...study(),
      report_summary: { ...contract.groff_failed, recovery: { available: false, affected_count: 0, last: null } },
    });
    render(<ValidationStudyPage />);
    await waitFor(() => expect(screen.getByText("Reproduction not attempted")).toBeInTheDocument());
    expect(screen.queryByTestId("recovery-notice")).not.toBeInTheDocument();
  });
});

// plan_8_2 section 4.1: a paper outside bioAF's methods (study 46's shape). The labels are pending the
// owner's sign-off.
describe("a paper outside bioAF's methods", () => {
  function outside() {
    return {
      ...study(),
      id: 46,
      classification: "missing_data",
      plan: {
        pipeline_key: null,
        blockers: ["No data accession or repository deposit is named in the paper.", "no nf-core equivalent for method: qRT-PCR"],
        ai_decisions: [],
      },
      report_summary: contract.outside_methods,
    };
  }

  test("leads with what applies and says nothing about sequencing reads", async () => {
    mockGet.mockResolvedValue(outside());
    render(<ValidationStudyPage />);
    expect(await screen.findByText("Outside bioAF's current validation methods")).toBeInTheDocument();
    expect(screen.getAllByText(/No eligible findings were identified for bioAF's current validation methods/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/raw sequencing reads/)).not.toBeInTheDocument();
  });

  test("lists the unsupported assay once, and keeps the requirements that do not apply in one collapsed detail", async () => {
    mockGet.mockResolvedValue(outside());
    render(<ValidationStudyPage />);
    await screen.findByText("Outside bioAF's current validation methods");
    expect(screen.getAllByText("bioAF has no validation method for qRT-PCR (experiment e1).").length).toBeGreaterThan(0);
    const grouped = screen.getByTestId("blockers-not-applying");
    expect(grouped).toHaveTextContent("2 requirements that do not apply");
    expect(screen.queryAllByTestId("blocker-withheld")).toHaveLength(0);
  });
});

// plan_8_2 section 4.2 (approved 2026-09-14): the scorecard and a strip of decisions, then four sections.
describe("the report's layout", () => {
  function groff() {
    return {
      ...study(),
      report_summary: {
        ...contract.scorecard_groff,
        recovery: { available: true, affected_count: 8, restate: null, last: null },
      },
    };
  }

  test("opens on the scorecard, with the outcome and its units in it, then a strip of decisions", async () => {
    mockGet.mockResolvedValue(groff());
    render(<ValidationStudyPage />);
    const card = await screen.findByRole("region", { name: "Findings Scorecard" });
    expect(within(card).getByTestId("scorecard-units")).toBeInTheDocument();
    expect(within(card).queryByTestId("scorecard-unassessed")).not.toBeInTheDocument();
    const decisions = screen.getByTestId("needs-a-decision");
    expect(within(decisions).getByRole("heading", { name: "Needs a decision" })).toBeInTheDocument();
    expect(within(decisions).getByRole("button", { name: "Review the re-evaluation" })).toBeInTheDocument();
  });

  test("then Findings, open, and three collapsed sections, each with its summary", async () => {
    mockGet.mockResolvedValue(groff());
    render(<ValidationStudyPage />);
    const findings = await screen.findByTestId("report-section-findings");
    expect(findings).toHaveAttribute("open");
    expect(within(findings).getByText(contract.scorecard_groff.sections.findings.summary)).toBeInTheDocument();
    for (const id of ["data", "checks", "diagnostics"]) {
      expect(screen.getByTestId(`report-section-${id}`)).not.toHaveAttribute("open");
    }
    expect(screen.getByRole("heading", { name: "Data and code" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Checks performed" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Run diagnostics" })).toBeInTheDocument();
  });

  test("a link to a finding opens it", async () => {
    Element.prototype.scrollIntoView = jest.fn();
    mockGet.mockResolvedValue(groff());
    render(<ValidationStudyPage />);
    const shared = await screen.findByTestId("shared-reason-R1");
    fireEvent.click(within(shared).getByRole("link", { name: "F2" }));
    expect(document.getElementById("finding-F2")).toHaveAttribute("open");
  });
});
