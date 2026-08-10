import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: mockPush }) }));
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));

import { GlobalSearch } from "./GlobalSearch";
import { api } from "@/lib/api";

const mockGet = api.get as jest.Mock;

beforeEach(() => {
  mockGet.mockReset();
  mockPush.mockReset();
});

test("searches by name after the user pauses and shows results", async () => {
  mockGet.mockResolvedValue({
    results: [{ entity_type: "experiment", entity_id: 5, name: "Alpha Study", experiment_id: 5 }],
  });
  render(<GlobalSearch debounceMs={10} />);

  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "alpha" } });

  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/search/quick?q=alpha"));
  expect(await screen.findByText("Alpha Study")).toBeInTheDocument();
});

test("clicking a result navigates to it and clears the box", async () => {
  mockGet.mockResolvedValue({
    results: [{ entity_type: "file", entity_id: 77, name: "reads.fastq.gz" }],
  });
  render(<GlobalSearch debounceMs={10} />);
  const input = screen.getByRole("searchbox") as HTMLInputElement;

  fireEvent.change(input, { target: { value: "reads" } });
  fireEvent.click(await screen.findByText("reads.fastq.gz"));

  expect(mockPush).toHaveBeenCalledWith("/data/files?file=77");
  expect(input.value).toBe("");
});

test("does not search for a blank query", async () => {
  render(<GlobalSearch debounceMs={10} />);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "   " } });
  await new Promise((r) => setTimeout(r, 40));
  expect(mockGet).not.toHaveBeenCalled();
});

test("shows a no-matches message when nothing is found", async () => {
  mockGet.mockResolvedValue({ results: [] });
  render(<GlobalSearch debounceMs={10} />);
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "zzz" } });
  expect(await screen.findByText(/no matches/i)).toBeInTheDocument();
});

test("pressing Enter on a non-empty query opens the full search page", () => {
  mockGet.mockResolvedValue({ results: [] });
  render(<GlobalSearch debounceMs={10} />);
  const input = screen.getByRole("searchbox");

  fireEvent.change(input, { target: { value: "common term" } });
  fireEvent.keyDown(input, { key: "Enter" });

  expect(mockPush).toHaveBeenCalledWith("/search?q=common%20term");
});

test("pressing Enter on a blank query does not navigate", () => {
  render(<GlobalSearch debounceMs={10} />);
  const input = screen.getByRole("searchbox");

  fireEvent.change(input, { target: { value: "   " } });
  fireEvent.keyDown(input, { key: "Enter" });

  expect(mockPush).not.toHaveBeenCalled();
});

// Below md the header cannot fit a text field beside its seven other controls: the
// field measured 26 x 34 px on the deployed app at both 375 and 768, which reads as an
// empty white rectangle rather than as a search box. It is a button at those widths,
// and the field it opens is `fixed` across the header rather than absolutely positioned
// inside the 26px parent, which would only reproduce the same width.
describe("the small-viewport field", () => {
  test("is behind a button that reports whether it is open", () => {
    render(<GlobalSearch debounceMs={10} />);
    const toggle = screen.getByTestId("global-search-toggle");

    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("global-search-field").className).toContain("hidden");
  });

  test("opens the field, focuses it, and breaks out of the collapsed parent", () => {
    render(<GlobalSearch debounceMs={10} />);
    fireEvent.click(screen.getByTestId("global-search-toggle"));

    expect(screen.getByTestId("global-search-toggle")).toHaveAttribute("aria-expanded", "true");
    const field = screen.getByTestId("global-search-field");
    expect(field.className).toContain("fixed");
    expect(field.className).not.toContain("hidden");
    expect(screen.getByRole("searchbox")).toHaveFocus();
  });

  test("closes again, so the field never sits over the header it covers", () => {
    render(<GlobalSearch debounceMs={10} />);
    fireEvent.click(screen.getByTestId("global-search-toggle"));
    fireEvent.click(screen.getByLabelText("Close search"));

    expect(screen.getByTestId("global-search-toggle")).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("global-search-field").className).toContain("hidden");
  });

  test("Escape closes it", () => {
    render(<GlobalSearch debounceMs={10} />);
    fireEvent.click(screen.getByTestId("global-search-toggle"));

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.getByTestId("global-search-toggle")).toHaveAttribute("aria-expanded", "false");
  });

  test("stays laid out at md and above regardless of the toggle", () => {
    render(<GlobalSearch debounceMs={10} />);

    // `hidden` only applies below md; `md:flex` is what puts it back.
    expect(screen.getByTestId("global-search-field").className).toContain("md:flex");
    expect(screen.getByTestId("global-search-toggle").className).toContain("md:hidden");
  });

  test("navigating from a result closes it", async () => {
    mockGet.mockResolvedValue({
      results: [{ entity_type: "file", entity_id: 3, name: "reads.fastq.gz" }],
    });
    render(<GlobalSearch debounceMs={10} />);
    fireEvent.click(screen.getByTestId("global-search-toggle"));
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "reads" } });

    fireEvent.click(await screen.findByText("reads.fastq.gz"));

    expect(screen.getByTestId("global-search-toggle")).toHaveAttribute("aria-expanded", "false");
  });
});
