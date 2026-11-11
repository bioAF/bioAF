/**
 * The QC summary supports exactly one piece of formatting: complete `**...**`
 * spans become strong text. Summaries can come from a custom pipeline's own
 * output or from an exception message, so every other character, including
 * Markdown- and HTML-shaped text, must stay literal.
 */
import { render } from "@testing-library/react";
import { GenericQCDashboard } from "./GenericQCDashboard";
import type { QCDashboardResponse } from "@/lib/types";

function renderSummary(summary_text: string) {
  const dashboard = {
    summary_text,
    raw_metrics: {},
    qc_config: { sections: [], metrics: {}, charts: [] },
  } as unknown as QCDashboardResponse;
  const { container } = render(<GenericQCDashboard dashboard={dashboard} />);
  return container.querySelector("p") as HTMLParagraphElement;
}

function hasCreatedElements(p: HTMLElement) {
  return p.querySelector("a, img, h1, h2, h3, h4, h5, h6, ul, ol, li, code, pre, script, em, b, i") !== null;
}

function hasEventHandlerAttribute(root: HTMLElement): boolean {
  return Array.from(root.querySelectorAll("*")).some((el) =>
    Array.from(el.attributes).some((a) => a.name.toLowerCase().startsWith("on")),
  );
}

describe("QC summary text", () => {
  test("built-in template summary keeps its bold values", () => {
    const p = renderSummary(
      "This bulk RNA-seq run analyzed **6 samples** with a mean of **21,400,000 reads per sample**. Overall quality: **Good**.",
    );
    expect(Array.from(p.querySelectorAll("strong")).map((s) => s.textContent)).toEqual([
      "6 samples",
      "21,400,000 reads per sample",
      "Good",
    ]);
    expect(p.textContent).toBe(
      "This bulk RNA-seq run analyzed 6 samples with a mean of 21,400,000 reads per sample. Overall quality: Good.",
    );
  });

  test("custom pipeline summary keeps its bold value", () => {
    const p = renderSummary("Custom pipeline run. Overall quality: **Pass**.");
    expect(p.querySelector("strong")).toHaveTextContent("Pass");
    expect(p.textContent).toBe("Custom pipeline run. Overall quality: Pass.");
  });

  test("generation failure text stays literal", () => {
    const text = "Generation failed: KeyError('<img src=x onerror=alert(1)>')";
    const p = renderSummary(text);
    expect(p.textContent).toBe(text);
    expect(p.querySelector("img")).toBeNull();
  });

  test.each([
    ["Markdown link", "[Re-authenticate](https://evil.example)"],
    ["Markdown image", "![Tracker](https://tracker.example/image)"],
    ["heading", "# Heading"],
    ["list", "- item one"],
    ["ordered list", "1. first"],
    ["inline code", "`code`"],
    ["italic", "*italic* and _italic_"],
    ["bare URL", "see https://evil.example/path"],
    ["raw HTML", '<a href="https://evil.example">Sign in</a><script>alert(1)</script>'],
    ["event handler", '<img src=x onerror="alert(1)">'],
  ])("%s stays literal text", (_label, text) => {
    const p = renderSummary(text);
    expect(p.textContent).toBe(text);
    expect(hasCreatedElements(p)).toBe(false);
    expect(hasEventHandlerAttribute(p)).toBe(false);
  });

  test("HTML inside a bold span is bold text, not markup", () => {
    const p = renderSummary('Quality: **<img src=x onerror="alert(1)">**.');
    const strong = p.querySelector("strong");
    expect(strong).not.toBeNull();
    expect(strong!.textContent).toBe('<img src=x onerror="alert(1)">');
    expect(p.querySelector("img")).toBeNull();
  });

  test("a link inside a bold span stays literal inside the strong text", () => {
    const p = renderSummary("**[click](https://evil.example)**");
    expect(p.querySelector("strong")!.textContent).toBe("[click](https://evil.example)");
    expect(p.querySelector("a")).toBeNull();
  });

  test("unmatched markers stay literal", () => {
    const p = renderSummary("Ratio **3 and ** more **");
    expect(p.querySelector("strong")!.textContent).toBe("3 and ");
    expect(p.textContent).toBe("Ratio 3 and  more **");
    expect(renderSummary("only **open").textContent).toBe("only **open");
    expect(renderSummary("only **open").querySelector("strong")).toBeNull();
  });

  test("a bold span does not cross a line boundary", () => {
    const p = renderSummary("first **half\nsecond** half");
    expect(p.querySelector("strong")).toBeNull();
    expect(p.textContent).toBe("first **half\nsecond** half");
  });

  test("an empty pair of markers is not a bold span", () => {
    const p = renderSummary("a **** b");
    expect(p.textContent).toBe("a **** b");
    expect(p.querySelector("strong")).toBeNull();
  });
});
