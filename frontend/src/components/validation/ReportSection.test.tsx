/**
 * plan_8_2 section 4.2 (approved 2026-09-14): each report section is a native disclosure with its summary
 * and counts always visible, and a link into a closed section opens it.
 */
import { fireEvent, render, screen } from "@testing-library/react";

import { ReportSection, useDisclosureLinks } from "./ReportSection";

test("the summary and counts show while the detail is collapsed", () => {
  render(
    <ReportSection
      id="data"
      title="Data and code"
      summary="2 resources: 1 deposit (1 bioAF reads)."
      counts={[
        { label: "1 deposit", tone: null },
        { label: "1 readable", tone: "ok" },
      ]}
    >
      <p>the resource table</p>
    </ReportSection>,
  );
  const section = screen.getByTestId("report-section-data");
  expect(section.tagName).toBe("DETAILS");
  expect(section).not.toHaveAttribute("open");
  expect(screen.getByRole("heading", { name: "Data and code" })).toBeInTheDocument();
  expect(screen.getByText("2 resources: 1 deposit (1 bioAF reads).")).toBeInTheDocument();
  expect(screen.getByText("1 readable")).toBeInTheDocument();
});

test("a section can open by default", () => {
  render(
    <ReportSection id="findings" title="Findings" summary="x" counts={[]} defaultOpen>
      <p>rows</p>
    </ReportSection>,
  );
  expect(screen.getByTestId("report-section-findings")).toHaveAttribute("open");
});

function Linked() {
  useDisclosureLinks(true);
  return (
    <div>
      <a href="#claim-3">Evidence</a>
      <ReportSection id="findings" title="Findings" summary="x" counts={[]}>
        <details id="finding-F2">
          <summary>F2</summary>
          <div id="claim-3">claim 3</div>
        </details>
      </ReportSection>
    </div>
  );
}

test("following a link opens every disclosure around its target", () => {
  Element.prototype.scrollIntoView = jest.fn();
  render(<Linked />);
  fireEvent.click(screen.getByRole("link", { name: "Evidence" }));
  expect(screen.getByTestId("report-section-findings")).toHaveAttribute("open");
  expect(document.getElementById("finding-F2")).toHaveAttribute("open");
  expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
});
