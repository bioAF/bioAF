import { render, screen } from "@testing-library/react";
import { NotSet } from "./NotSet";
import { NOT_SET } from "@/lib/placeholders";

// The placeholder was a dash, then the words "NOT SET", then a dash again on the
// owner's ruling of 2026-08-07. These assert what has to hold under any of them,
// which is why they read the glyph from the constant rather than pinning it: it
// comes from one place, it is legible, and it is not silent to a screen reader.

test("renders the shared placeholder rather than its own", () => {
  render(<NotSet />);

  expect(screen.getByText(NOT_SET)).toBeInTheDocument();
});

test("draws the placeholder legibly", () => {
  render(<NotSet />);

  // gray-600 on white is 6.87:1, and it carries a dark token so it holds in both
  // themes. The gray-300 the original dashes were drawn in was 1.47:1, which is
  // why an empty cell could not be told apart from a rendering bug.
  expect(screen.getByText(NOT_SET).className).toContain("text-gray-600");
});

test("a caller can say what is not set, for a screen reader", () => {
  // A dash reads as nothing, so an unlabelled one leaves a screen reader user
  // with no idea which field is empty.
  render(<NotSet label="Organism" />);

  expect(screen.getByText(NOT_SET)).toHaveAttribute("aria-label", "Organism not set");
});

test("is not announced as content when the caller gives it no label", () => {
  const { container } = render(<NotSet />);

  expect(container.querySelector("span")).toHaveAttribute("role", "presentation");
});
