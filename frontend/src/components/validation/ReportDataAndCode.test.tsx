/**
 * plan_8_2 section 4.2 (approved 2026-09-14): resources bioAF has no adapter for are one compact group with
 * links and never hidden; every resource stays reachable. Rendered from the backend's projection.
 */
import { fireEvent, render, screen, within } from "@testing-library/react";

import contract from "./__fixtures__/reportContract.json";
import type { ReportSummary } from "@/lib/validationReport";
import { ReportDataAndCode } from "./ReportDataAndCode";

const stage2 = contract.stage2_selection as unknown as ReportSummary;

test("resources bioAF has no adapter for are one compact group with links", () => {
  render(<ReportDataAndCode summary={stage2} />);
  const group = screen.getByTestId("resources-unsupported");
  expect(group).toHaveTextContent("Resources bioAF has no adapter for");
  const link = within(group).getByRole("link", { name: "PXD099001" });
  expect(link).toHaveAttribute("href", "https://www.ebi.ac.uk/pride/archive/projects/PXD099001");
  expect(group).toHaveTextContent(/no PRIDE adapter/);
});

test("the readable deposit is in the table, and every resource stays one expander away", () => {
  render(<ReportDataAndCode summary={stage2} />);
  const table = screen.getByTestId("resources-readable");
  expect(within(table).getByText("GSE555001")).toBeInTheDocument();
  expect(within(table).queryByText("PXD099001")).not.toBeInTheDocument();
  const every = screen.getByTestId("resources-every") as HTMLDetailsElement;
  expect(every.tagName).toBe("DETAILS");
  every.open = true;
  fireEvent(every, new Event("toggle"));
  expect(within(every).getByText("PXD099001")).toBeInTheDocument();
});
