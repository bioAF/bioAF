/**
 * plan_8_2 section 3.1: an unresolved check against a table offers a person the recorded confirmation, and a
 * reading a person recorded is shown with who recorded it and why. The labels are pending the owner's
 * sign-off.
 */
import { render, screen, within } from "@testing-library/react";

import contract from "./__fixtures__/reportContract.json";
import type { ReportSummary } from "@/lib/validationReport";
import { ClaimSelection } from "./ClaimSelection";

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, roleName: "admin", loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { post: jest.fn() } }));

const stage2 = contract.stage2_selection as unknown as ReportSummary;

const withConsistency = (consistency: Record<string, unknown>) =>
  ({
    ...stage2,
    claims: stage2.claims.map((claim, i) =>
      i === 0
        ? {
            ...claim,
            contrast: "KO vs WT (day 7)",
            consistency: {
              outcome: "unresolved",
              label: "Unresolved against the authors' results",
              reason: "the table is headerless, and nothing bioAF holds says which of its 7 columns holds the identifier",
              table: "diff.txt.gz",
              source: "deposit",
              rows_tested: 0,
              rows_passing: null,
              rows_missing: 0,
              count_range: null,
              candidates: [],
              assumptions: [],
              binding: null,
              superseded: null,
              interpretation: null,
              candidate_roles: { id: [0], lfc: [1, 2], pvalue: [2], padj: [2] },
              columns_count: 7,
              ...consistency,
            },
          }
        : claim,
    ),
  }) as unknown as ReportSummary;

it("offers the recorded confirmation on an unresolved check when the page can act", () => {
  render(<ClaimSelection summary={withConsistency({})} studyId={7} onChanged={jest.fn()} />);
  const row = within(screen.getByTestId("claim-0"));
  expect(row.getByRole("button", { name: "Record how this table reads" })).toBeInTheDocument();
});

it("offers nothing where the page cannot act, or where the check is not unresolved", () => {
  const { unmount } = render(<ClaimSelection summary={withConsistency({})} />);
  expect(screen.queryByRole("button", { name: "Record how this table reads" })).not.toBeInTheDocument();
  unmount();
  render(<ClaimSelection summary={withConsistency({ outcome: "agree" })} studyId={7} onChanged={jest.fn()} />);
  const row = within(screen.getByTestId("claim-0"));
  expect(row.queryByRole("button", { name: "Record how this table reads" })).not.toBeInTheDocument();
});

it("shows a reading a person recorded, with who recorded it and why", () => {
  render(
    <ClaimSelection
      summary={withConsistency({
        outcome: "agree",
        interpretation: {
          source: "confirmation",
          version: 1,
          columns: { pvalue: "column 6" },
          effect_scale: "log2",
          evidence: ["recorded by reviewer@example.org: The series README lists the columns."],
        },
      })}
    />,
  );
  expect(within(screen.getByTestId("claim-0")).getByTestId("consistency-interpretation")).toHaveTextContent(
    "Table read as recorded by reviewer@example.org: The series README lists the columns.",
  );
});
