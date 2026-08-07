import { render, screen } from "@testing-library/react";
import { NotSet } from "./NotSet";

test("says the value is not set, in words", () => {
  render(<NotSet />);

  expect(screen.getByText("NOT SET")).toBeInTheDocument();
});

test("is not a dash", () => {
  const { container } = render(<NotSet />);

  expect(container.textContent).not.toContain("—");
  expect(container.textContent).not.toContain("-");
});

test("reads as quieter than a real value without dropping below AA", () => {
  render(<NotSet />);

  // gray-600 on white is 5.9:1. The gray-300 this replaces was 1.47:1, which
  // is why an empty cell was indistinguishable from a rendering bug.
  expect(screen.getByText("NOT SET").className).toContain("text-gray-600");
});

test("a caller can say what is not set, for a screen reader", () => {
  render(<NotSet label="Organism" />);

  expect(screen.getByText("NOT SET")).toHaveAttribute("aria-label", "Organism not set");
});
