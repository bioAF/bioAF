import { render, screen } from "@testing-library/react";
import { Card } from "./Card";

test("renders its content on a themed surface rather than a hardcoded white", () => {
  render(<Card>Sample QC</Card>);

  const card = screen.getByText("Sample QC");
  expect(card.className).toContain("bg-surface");
  expect(card.className).not.toContain("bg-white");
});

test("a caller can still title the region, and the title names it", () => {
  render(<Card title="Recent runs">body</Card>);

  const region = screen.getByRole("region", { name: "Recent runs" });
  expect(region).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Recent runs" })).toBeInTheDocument();
});

test("an untitled card is not announced as an anonymous region", () => {
  render(<Card>body</Card>);

  expect(screen.queryByRole("region")).not.toBeInTheDocument();
});

test("padding is a choice, and extra classes survive", () => {
  const { rerender } = render(
    <Card padding="sm" className="col-span-2">
      body
    </Card>,
  );
  const card = screen.getByText("body");
  expect(card.className).toContain("p-4");
  expect(card.className).toContain("col-span-2");

  rerender(<Card padding="none">body</Card>);
  expect(screen.getByText("body").className).not.toContain("p-4");
});
