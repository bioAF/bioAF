/**
 * plan_8 section 7: the literature validation list replaces its Outcome column with a compact
 * Validation Scorecard, keeps each study's lifecycle state in its own column, and reads every
 * summary from the one list response.
 */
import { render, screen, waitFor, within } from "@/testing/renderWithProviders";

import { NOT_SET } from "@/lib/placeholders";
import ValidationStudiesListPage from "./page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn() }),
  usePathname: () => "/lab-knowledge/validation-studies",
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

function compact(overrides: Record<string, unknown>) {
  return {
    status: "scored",
    status_label: null,
    score: 66.67,
    display_score: 67,
    score_label: "67 / 100",
    assessed_count: 5,
    total_count: 5,
    scope_label: "5 / 5 assessed",
    primary_discrepancy_count: 0,
    primary_unassessed_count: 0,
    indicators: [],
    in_progress: false,
    in_progress_label: null,
    rubric_version: 1,
    inventory_revision: 1,
    ...overrides,
  };
}

const ROWS = [
  {
    id: 41,
    state: "classified",
    title: "A scored paper",
    classification: "inconclusive",
    source_doi: "10.1/a",
    scorecard: compact({ primary_discrepancy_count: 1, indicators: [{ kind: "primary_discrepancy", text: "Primary discrepancy" }] }),
  },
  {
    id: 42,
    state: "running",
    title: "A running paper",
    source_doi: "10.1/b",
    scorecard: compact({
      status: "not_assessed",
      score: null,
      display_score: null,
      score_label: null,
      assessed_count: 0,
      total_count: 7,
      scope_label: "0 / 7 assessed",
      primary_unassessed_count: 2,
      indicators: [{ kind: "primary_unassessed", text: "Primary findings remain unassessed" }],
      in_progress: true,
      in_progress_label: "In progress",
    }),
  },
  {
    id: 43,
    state: "classified",
    title: "A paper read before the scorecard",
    source_doi: "10.1/c",
    scorecard: compact({
      status: "unavailable",
      status_label: "Score unavailable for this historical report.",
      score: null,
      display_score: null,
      score_label: null,
      assessed_count: null,
      total_count: null,
      scope_label: null,
      primary_discrepancy_count: null,
      primary_unassessed_count: null,
      inventory_revision: null,
    }),
  },
];

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockResolvedValue(ROWS);
});

function row(title: string): HTMLElement {
  return screen.getByText(title).closest("tr") as HTMLElement;
}

test("the Outcome column is replaced by the Validation Scorecard", async () => {
  render(<ValidationStudiesListPage />);
  await screen.findByText("A scored paper");
  expect(screen.getByRole("columnheader", { name: "Validation Scorecard" })).toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "Outcome" })).not.toBeInTheDocument();
});

test("a scored study shows both metrics, and a primary discrepancy is not hidden by the score", async () => {
  render(<ValidationStudiesListPage />);
  await screen.findByText("A scored paper");
  const scored = row("A scored paper");
  expect(within(scored).getByText("67 / 100")).toBeInTheDocument();
  expect(within(scored).getByText("5 / 5 assessed")).toBeInTheDocument();
  expect(within(scored).getByText("Primary discrepancy")).toBeInTheDocument();
});

test("lifecycle state stays in its own column beside the scorecard", async () => {
  render(<ValidationStudiesListPage />);
  await screen.findByText("A running paper");
  expect(screen.getByRole("columnheader", { name: "Status" })).toBeInTheDocument();
  const running = row("A running paper");
  expect(within(running).getByText("Running analysis")).toBeInTheDocument();
  expect(within(running).getByText("0 / 7 assessed")).toBeInTheDocument();
  expect(within(running).getByText("Primary findings remain unassessed")).toBeInTheDocument();
  expect(within(running).getByText("In progress")).toBeInTheDocument();
});

test("no score renders as a dash, never undefined, NaN or a zero", async () => {
  render(<ValidationStudiesListPage />);
  await screen.findByText("A running paper");
  const running = row("A running paper");
  expect(within(running).getByLabelText("Overall score: not assessed")).toHaveTextContent(NOT_SET);
  const historical = row("A paper read before the scorecard");
  expect(within(historical).getByText("Score unavailable for this historical report.")).toBeInTheDocument();
  for (const title of ["A scored paper", "A running paper", "A paper read before the scorecard"]) {
    expect(row(title).textContent).not.toMatch(/undefined|NaN|0 \/ 100/);
  }
});

test("every summary comes from the one list request, never a report per row", async () => {
  render(<ValidationStudiesListPage />);
  await screen.findByText("A paper read before the scorecard");
  await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1));
  expect(mockGet).toHaveBeenCalledWith("/api/validation-studies");
});

test("each row links to its full report", async () => {
  render(<ValidationStudiesListPage />);
  await screen.findByText("A scored paper");
  expect(screen.getByRole("link", { name: "A scored paper" })).toHaveAttribute("href", "/lab-knowledge/validation-studies/41");
});
