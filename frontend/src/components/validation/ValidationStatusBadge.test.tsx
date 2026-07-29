import { render, screen } from "@testing-library/react";
import { ValidationStatusBadge } from "./ValidationStatusBadge";

test("renders the confidence-derived label", () => {
  render(<ValidationStatusBadge confidence={100} />);
  expect(screen.getByText("Fully Validated")).toBeInTheDocument();
});

test("a null confidence reads Could Not Reproduce", () => {
  render(<ValidationStatusBadge confidence={null} />);
  expect(screen.getByText("Could Not Reproduce")).toBeInTheDocument();
});

test("the partially_reproduced classification overrides the band with a precise label", () => {
  render(<ValidationStatusBadge confidence={60} classification="partially_reproduced" />);
  expect(screen.getByText("Partially Reproduced")).toBeInTheDocument();
  expect(screen.getByText("Needs review")).toBeInTheDocument();
});
