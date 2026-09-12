/**
 * change_7.3 sections 10 and 11: the study page renders the report projection the API returns.
 *
 * The study is Groff on the current build with the attachment bundle failing: nothing executed, the
 * raw reads sit under controlled access in an archive bioAF cannot read, and no claim was compared.
 */
import { act, render, screen, waitFor } from "@/testing/renderWithProviders";

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
