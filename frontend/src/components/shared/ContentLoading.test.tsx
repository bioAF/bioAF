import { render, screen } from "@testing-library/react";
import { ContentLoading } from "./ContentLoading";

test("says it is loading, for anyone who cannot see the shapes", () => {
  render(<ContentLoading />);

  const status = screen.getByRole("status");
  expect(status).toHaveAttribute("aria-live", "polite");
  expect(status).toHaveTextContent(/loading/i);
});

test("a caller's message is what gets announced", () => {
  render(<ContentLoading message="Loading pipeline runs" />);

  expect(screen.getByRole("status")).toHaveTextContent("Loading pipeline runs");
});

test("draws the shape of a table when that is what is coming", () => {
  render(<ContentLoading variant="table" rows={4} />);

  const rows = screen.getAllByTestId("skeleton-row");
  expect(rows).toHaveLength(4);
});

test("draws cards when a grid is coming", () => {
  render(<ContentLoading variant="cards" rows={3} />);

  expect(screen.getAllByTestId("skeleton-card")).toHaveLength(3);
});

test("the placeholder shapes are hidden from assistive tech", () => {
  render(<ContentLoading variant="table" />);

  for (const row of screen.getAllByTestId("skeleton-row")) {
    expect(row).toHaveAttribute("aria-hidden", "true");
  }
});

test("the shapes pulse, and stop pulsing under reduced motion", () => {
  render(<ContentLoading variant="table" rows={1} />);

  // The global reduced-motion rule covers animate-pulse (see
  // a11y-reduced-motion.test.ts); this only checks the utility is the one that
  // rule knows about.
  expect(screen.getAllByTestId("skeleton-row")[0].className).toContain("animate-pulse");
});
