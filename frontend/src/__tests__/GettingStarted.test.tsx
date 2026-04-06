import { render, screen, fireEvent } from "@testing-library/react";
import { GettingStarted } from "@/components/onboarding/GettingStarted";

// Mock next/navigation
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
}));

// Suppress img loading errors in JSDOM
beforeAll(() => {
  jest.spyOn(console, "error").mockImplementation(() => {});
});
afterAll(() => {
  jest.restoreAllMocks();
});

describe("GettingStarted", () => {
  const onComplete = jest.fn();

  beforeEach(() => {
    onComplete.mockReset();
  });

  it("renders the first slide with correct title", () => {
    render(<GettingStarted onComplete={onComplete} />);
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
  });

  it("has next/prev navigation buttons", () => {
    render(<GettingStarted onComplete={onComplete} />);
    expect(screen.getByLabelText("Next slide")).toBeInTheDocument();
    expect(screen.getByLabelText("Previous slide")).toBeDisabled();
  });

  it("navigates forward and backward with buttons", () => {
    render(<GettingStarted onComplete={onComplete} />);

    fireEvent.click(screen.getByLabelText("Next slide"));
    expect(screen.getByText("Experiments")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Previous slide"));
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
  });

  it("navigates with keyboard arrows", () => {
    render(<GettingStarted onComplete={onComplete} />);

    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByText("Experiments")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
  });

  it('"Skip tour" link is always visible', () => {
    render(<GettingStarted onComplete={onComplete} />);
    expect(screen.getByText("Skip tour")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Next slide"));
    expect(screen.getByText("Skip tour")).toBeInTheDocument();
  });

  it("skip tour calls onComplete", () => {
    render(<GettingStarted onComplete={onComplete} />);
    fireEvent.click(screen.getByText("Skip tour"));
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("final slide shows Go to Dashboard button", () => {
    render(<GettingStarted onComplete={onComplete} />);

    // Navigate to last slide (13th = index 12)
    for (let i = 0; i < 12; i++) {
      fireEvent.click(screen.getByLabelText("Next slide"));
    }

    expect(screen.getByText("Roles & Permissions")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /go to dashboard/i })).toBeInTheDocument();
  });

  it("Go to Dashboard calls onComplete", () => {
    render(<GettingStarted onComplete={onComplete} />);

    for (let i = 0; i < 12; i++) {
      fireEvent.click(screen.getByLabelText("Next slide"));
    }

    fireEvent.click(screen.getByRole("button", { name: /go to dashboard/i }));
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it('standalone mode shows "Close" instead of "Go to Dashboard"', () => {
    render(<GettingStarted onComplete={onComplete} standalone />);

    for (let i = 0; i < 12; i++) {
      fireEvent.click(screen.getByLabelText("Next slide"));
    }

    expect(screen.queryByText(/go to dashboard/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /close/i })).toBeInTheDocument();
  });

  it("renders 13 dot indicators", () => {
    render(<GettingStarted onComplete={onComplete} />);
    const dots = screen.getAllByTestId("slide-dot");
    expect(dots).toHaveLength(13);
  });

  it("renders highlight overlays on slides that have them", () => {
    render(<GettingStarted onComplete={onComplete} />);
    // Dashboard slide has 7 highlights
    const highlights = screen.getAllByTestId("highlight");
    expect(highlights.length).toBe(7);
    expect(screen.getByText("Sidebar")).toBeInTheDocument();
    expect(screen.getByText("Running Jobs")).toBeInTheDocument();
  });

  it("does not render highlights on slides without them", () => {
    render(<GettingStarted onComplete={onComplete} />);

    // Advance to "Pipeline Runs" (index 3) which has no highlights
    for (let i = 0; i < 3; i++) {
      fireEvent.click(screen.getByLabelText("Next slide"));
    }

    expect(screen.getByText("Pipeline Runs")).toBeInTheDocument();
    expect(screen.queryAllByTestId("highlight")).toHaveLength(0);
  });
});
