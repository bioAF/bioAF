import { render, screen } from "@testing-library/react";
import { StatusBadge } from "@/components/shared/StatusBadge";

describe("StatusBadge", () => {
  it("defaults to the generic palette and humanizes the label", () => {
    render(<StatusBadge status="awaiting_confirmation" />);
    const el = screen.getByText("awaiting confirmation");
    expect(el).toHaveClass("bg-blue-100", "text-blue-800");
  });

  it("falls back to neutral styling for an unknown status", () => {
    render(<StatusBadge status="mystery" />);
    expect(screen.getByText("mystery")).toHaveClass("bg-gray-100", "text-gray-600");
  });

  it("uses the entity palette when given (pipelineRun running is blue)", () => {
    render(<StatusBadge entity="pipelineRun" status="running" />);
    expect(screen.getByText("running")).toHaveClass("bg-blue-100", "text-blue-700");
  });

  it("the same status renders differently for a different entity", () => {
    render(<StatusBadge entity="computeSession" status="running" />);
    expect(screen.getByText("running")).toHaveClass("bg-green-100", "text-green-800");
  });
});
