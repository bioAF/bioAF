/**
 * plan_8_2 section 4.2 (approved 2026-09-14): the Findings section. Findings are grouped by the experiment
 * that reports them, each finding's claims nested inside it; a reason several findings share is stated once
 * and each of them points to it; a valid discrepancy opens by default. Rendered from the backend's
 * projection (`__fixtures__/reportContract.json`).
 */
import { fireEvent, render, screen, within } from "@testing-library/react";

import contract from "./__fixtures__/reportContract.json";
import type { ReportSummary } from "@/lib/validationReport";
import { ReportFindings } from "./ReportFindings";

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, roleName: "admin", loading: false }),
}));
jest.mock("@/lib/api", () => ({ api: { post: jest.fn() } }));

const groff = contract.scorecard_groff as unknown as ReportSummary;
const scored = contract.scorecard_scored as unknown as ReportSummary;
const long = contract.scorecard_long as unknown as ReportSummary;
const stage2 = contract.stage2_selection as unknown as ReportSummary;

test("findings are grouped by their experiment, and a shared reason is stated once", () => {
  render(<ReportFindings summary={groff} />);
  expect(screen.getByRole("heading", { name: "Experiment e1: bulk RNA-seq (nf-core/rnaseq)" })).toBeInTheDocument();
  const shared = screen.getByTestId("shared-reason-R1");
  expect(shared).toHaveTextContent(/controlled access/);
  const affects = within(shared).getAllByRole("link");
  expect(affects.map((a) => a.getAttribute("href"))).toEqual([
    "#finding-F1",
    "#finding-F2",
    "#finding-F3",
    "#finding-F4",
  ]);
  // Each affected finding points to the shared reason instead of repeating it.
  const f2 = document.getElementById("finding-F2") as HTMLElement;
  const f2Summary = f2.querySelector("summary") as HTMLElement;
  expect(within(f2Summary).getByRole("link", { name: "See R1" })).toHaveAttribute("href", "#shared-reason-R1");
  expect(f2Summary).not.toHaveTextContent(/controlled access/);
});

test("each finding holds its claims, reachable by their existing anchors", () => {
  render(<ReportFindings summary={groff} />);
  const f1 = document.getElementById("finding-F1") as HTMLElement;
  expect(f1.tagName).toBe("DETAILS");
  const held = groff.scorecard?.unassessed_items.find((item) => item.finding_id === "F1")?.claim_indices[0];
  expect(within(f1).getByTestId(`claim-${held}`)).toHaveAttribute("id", `claim-${held}`);
});

test("technical findings sit in their own collapsed group", () => {
  render(<ReportFindings summary={groff} />);
  const excluded = screen.getByTestId("findings-group-excluded");
  expect(excluded.tagName).toBe("DETAILS");
  expect(excluded).not.toHaveAttribute("open");
});

test("a valid discrepancy is open by default, and the rest are collapsed", () => {
  render(<ReportFindings summary={scored} />);
  expect(document.getElementById("finding-F5")).toHaveAttribute("open");
  expect(document.getElementById("finding-F1")).not.toHaveAttribute("open");
});

test("a long group shows five findings and says how many more", () => {
  render(<ReportFindings summary={long} />);
  const button = screen.getByRole("button", { name: "Show all (9 more)" });
  expect(button).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(button);
  expect(screen.getByRole("button", { name: "Show fewer" })).toHaveAttribute("aria-expanded", "true");
});

test("claims no finding holds stay reachable under their own heading", () => {
  render(<ReportFindings summary={stage2} />);
  const group = screen.getByTestId("findings-group-ungrouped");
  expect(within(group).getByTestId("claim-0")).toBeInTheDocument();
});
