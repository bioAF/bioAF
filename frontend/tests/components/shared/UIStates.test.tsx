import { render, screen, fireEvent } from "@testing-library/react";
import { ErrorState } from "@/components/shared/ErrorState";

describe("ErrorState", () => {
  it("renders error message", () => {
    render(<ErrorState message="Something went wrong" />);
    expect(screen.getByTestId("error-state")).toBeInTheDocument();
    expect(screen.getByTestId("error-message")).toHaveTextContent(
      "Something went wrong"
    );
  });

  it("calls onRetry when retry clicked", () => {
    const onRetry = jest.fn();
    render(<ErrorState message="Failed" onRetry={onRetry} />);
    fireEvent.click(screen.getByTestId("error-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("shows details when toggle clicked", () => {
    render(
      <ErrorState
        message="Error"
        details="Stack trace: TypeError at line 42"
      />
    );
    // Details hidden initially
    expect(screen.queryByTestId("error-details")).not.toBeInTheDocument();
    // Click toggle to show
    fireEvent.click(screen.getByTestId("error-details-toggle"));
    expect(screen.getByTestId("error-details")).toHaveTextContent(
      "Stack trace: TypeError at line 42"
    );
    // Click toggle to hide
    fireEvent.click(screen.getByTestId("error-details-toggle"));
    expect(screen.queryByTestId("error-details")).not.toBeInTheDocument();
  });

  it("renders without retry button when onRetry not provided", () => {
    render(<ErrorState message="Error occurred" />);
    expect(screen.queryByTestId("error-retry")).not.toBeInTheDocument();
  });
});
