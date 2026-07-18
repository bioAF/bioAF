import { render, screen } from "@testing-library/react";
import { ValidationStatusBadge } from "@/components/validation/ValidationStatusBadge";

describe("ValidationStatusBadge", () => {
  it("renders Fully Validated with no review flag at 100%", () => {
    render(<ValidationStatusBadge confidence={100} />);
    expect(screen.getByText("Fully Validated")).toBeInTheDocument();
    expect(screen.queryByText(/needs review/i)).not.toBeInTheDocument();
  });

  it("flags human review for a mid-band confidence", () => {
    render(<ValidationStatusBadge confidence={60} />);
    expect(screen.getByText("Possibly Validated")).toBeInTheDocument();
    expect(screen.getByText(/needs review/i)).toBeInTheDocument();
  });

  it("renders Could Not Reproduce (no review flag) when confidence is null", () => {
    render(<ValidationStatusBadge confidence={null} />);
    expect(screen.getByText("Could Not Reproduce")).toBeInTheDocument();
    expect(screen.queryByText(/needs review/i)).not.toBeInTheDocument();
  });

  it("exposes the band description as a tooltip", () => {
    render(<ValidationStatusBadge confidence={10} />);
    expect(screen.getByTitle(/5-24% confident/)).toHaveTextContent("Unlikely");
  });

  it("can suppress the review flag when showReview is false", () => {
    render(<ValidationStatusBadge confidence={60} showReview={false} />);
    expect(screen.getByText("Possibly Validated")).toBeInTheDocument();
    expect(screen.queryByText(/needs review/i)).not.toBeInTheDocument();
  });
});
