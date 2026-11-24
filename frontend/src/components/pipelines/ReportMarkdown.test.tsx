import { render, screen } from "@testing-library/react";

jest.unmock("react-markdown");
jest.unmock("remark-gfm");

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ReportMarkdown } from "./ReportMarkdown";

/**
 * The real react-markdown pipeline (micromark, mdast/hast conversion and
 * @ungap/structured-clone, hast-util-to-jsx-runtime with style-to-js), not a
 * mock: a transitive update that changes what a report or the settings release
 * notes show fails here.
 */

test("a pipeline report renders headings, emphasis, lists and code", () => {
  render(
    <ReportMarkdown
      source={"# Run summary\n\nAll **24** samples passed.\n\n- FastQC\n- MultiQC\n\n`nextflow run`"}
    />,
  );
  expect(screen.getByRole("heading", { level: 1, name: "Run summary" })).toBeInTheDocument();
  expect(screen.getByText("24").tagName).toBe("STRONG");
  expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["FastQC", "MultiQC"]);
  expect(screen.getByText("nextflow run").tagName).toBe("CODE");
});

test("a report keeps web links, opens them in a new tab, and drops script links", () => {
  render(
    <ReportMarkdown source={"[docs](https://nf-co.re) and [bad](javascript:alert(1))"} />,
  );
  const docs = screen.getByRole("link", { name: "docs" });
  expect(docs).toHaveAttribute("href", "https://nf-co.re");
  expect(docs).toHaveAttribute("target", "_blank");
  expect(screen.queryByRole("link", { name: "bad" })).not.toBeInTheDocument();
  expect(screen.getByText("bad")).toBeInTheDocument();
});

test("raw HTML in a report, including inline styles, is skipped", () => {
  const { container } = render(
    <ReportMarkdown source={'before <div style="color: red /* x */">styled</div> after'} />,
  );
  expect(container.querySelector("[style]")).toBeNull();
  expect(screen.getByTestId("markdown-report").querySelector("div")).toBeNull();
  expect(screen.getByTestId("markdown-report")).toHaveTextContent("before");
});

test("GitHub-flavoured tables keep their column alignment", () => {
  // The settings page renders release notes with remark-gfm; an aligned column
  // is the one place this pipeline produces a style for React.
  const { container } = render(
    <ReactMarkdown remarkPlugins={[remarkGfm]}>
      {"| Sample | Reads |\n| :--- | ---: |\n| S1 | 1,000 |"}
    </ReactMarkdown>,
  );
  const cells = container.querySelectorAll("td");
  expect([...cells].map((c) => c.textContent)).toEqual(["S1", "1,000"]);
  expect((cells[1] as HTMLElement).style.textAlign).toBe("right");
});
