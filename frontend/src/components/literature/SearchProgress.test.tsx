import { render, screen, fireEvent } from "@testing-library/react";
import {
  SearchProgress,
  searchSourceProgress,
  sourceChipClass,
} from "./SearchProgress";
import type { SearchSummary } from "@/lib/literature";

function status(per: Record<string, string>): SearchSummary {
  return {
    id: 1,
    query_text: "q",
    sources: [],
    per_source_status: per,
    status: "running",
    result_count: null,
    error_message: null,
    started_at: null,
    completed_at: null,
    created_at: "2026-07-31T00:00:00Z",
  };
}

describe("searchSourceProgress", () => {
  it("counts complete and failed sources as done (a failed source is finished)", () => {
    expect(
      searchSourceProgress({ pubmed: "complete", biorxiv: "failed: timeout", europepmc: "running", semantic_scholar: "queued" }),
    ).toEqual({ done: 2, total: 4 });
  });

  it("is 0 of 0 for an empty map", () => {
    expect(searchSourceProgress({})).toEqual({ done: 0, total: 0 });
  });
});

describe("sourceChipClass", () => {
  it("colors complete green, failed red, and anything else gray", () => {
    expect(sourceChipClass("complete")).toMatch(/green/);
    expect(sourceChipClass("failed: rate limit")).toMatch(/red/);
    expect(sourceChipClass("running")).toMatch(/gray/);
  });
});

describe("SearchProgress", () => {
  it("shows an honest N-of-M source count, per-source chips, and a Stop watching button", () => {
    const onStop = jest.fn();
    render(
      <SearchProgress
        status={status({ pubmed: "complete", biorxiv: "running", europepmc: "queued", semantic_scholar: "queued" })}
        onStop={onStop}
      />,
    );

    expect(screen.getByText(/1 of 4 sources/i)).toBeInTheDocument();
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "1");
    expect(bar).toHaveAttribute("aria-valuemax", "4");
    expect(screen.getByText("pubmed: complete")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /stop watching/i }));
    expect(onStop).toHaveBeenCalledTimes(1);
  });
});
