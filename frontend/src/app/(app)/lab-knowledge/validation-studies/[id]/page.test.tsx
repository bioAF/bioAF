/**
 * change_7.3 sections 10 and 11: the study page renders the report projection the API returns.
 *
 * The study is Groff on the current build with the attachment bundle failing: nothing executed, the
 * raw reads sit under controlled access in an archive bioAF cannot read, and no claim was compared.
 */
import { render, screen, waitFor } from "@/testing/renderWithProviders";

import contract from "@/components/validation/__fixtures__/reportContract.json";
import ValidationStudyPage from "./page";

jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "34" }),
  useRouter: () => ({ push: jest.fn(), back: jest.fn() }),
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
