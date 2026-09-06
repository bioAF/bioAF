import { render, screen } from "@testing-library/react";
import { ValidationStudyOutcome } from "@/components/validation/ValidationStudyOutcome";

describe("ValidationStudyOutcome", () => {
  it("renders the validation badge for a classified study (validated -> Fully Validated)", () => {
    render(<ValidationStudyOutcome state="classified" confidence={100} classification="validated" />);
    expect(screen.getByText("Fully Validated")).toBeInTheDocument();
    expect(screen.queryByText(/^Step /)).not.toBeInTheDocument();
  });

  it("shows Could Not Reproduce (with the bucket) for a classified, could-not-test study", () => {
    render(<ValidationStudyOutcome state="classified" confidence={null} classification="missing_data" />);
    expect(screen.getByText("Could Not Reproduce")).toBeInTheDocument();
    expect(screen.getByText("Missing data")).toBeInTheDocument();
  });

  it("shows the pipeline stage (not a validation badge) while a study is still running", () => {
    render(<ValidationStudyOutcome state="running" confidence={null} />);
    expect(screen.getByText("Running analysis")).toBeInTheDocument();
    expect(screen.getByText("Step 7 of 9")).toBeInTheDocument();
    // A null confidence on a running study must NOT read as "Could Not Reproduce".
    expect(screen.queryByText("Could Not Reproduce")).not.toBeInTheDocument();
  });

  it("surfaces the failure reason for an errored study", () => {
    render(<ValidationStudyOutcome state="error" confidence={null} failureReason="head pool never scaled up" />);
    expect(screen.getByText("Error")).toBeInTheDocument();
    expect(screen.getByText("head pool never scaled up")).toBeInTheDocument();
  });

  it("shows a declined stage for a declined plan", () => {
    render(<ValidationStudyOutcome state="plan_declined" confidence={null} />);
    expect(screen.getByText("Plan declined")).toBeInTheDocument();
    expect(screen.queryByText(/^Step /)).not.toBeInTheDocument();
  });
});

// ---- plan_7 step 9: which KIND of validation produced this verdict ----

describe("the route qualifier", () => {
  it("marks a deposit-route verdict as reached from deposited data", () => {
    render(
      <ValidationStudyOutcome state="classified" confidence={100} classification="validated" route="deposit" />,
    );
    expect(screen.getByText("Deposited data")).toBeInTheDocument();
  });

  it("marks a raw-reads verdict too, so neither reads as the default", () => {
    render(
      <ValidationStudyOutcome state="classified" confidence={100} classification="validated" route="pipeline" />,
    );
    expect(screen.getByText("Raw reads")).toBeInTheDocument();
  });

  it("says nothing for a study that predates the route", () => {
    // Every study before plan_7 has no route. Rendering a guess would be worse than rendering
    // nothing, because the qualifier is a claim about how much the verdict is worth.
    render(<ValidationStudyOutcome state="classified" confidence={100} classification="validated" />);
    expect(screen.queryByText("Deposited data")).not.toBeInTheDocument();
    expect(screen.queryByText("Raw reads")).not.toBeInTheDocument();
  });

  it("explains what the deposited-data qualifier means on hover", () => {
    render(
      <ValidationStudyOutcome state="classified" confidence={100} classification="validated" route="deposit" />,
    );
    expect(screen.getByText("Deposited data").getAttribute("title")).toMatch(/not independently repeated/i);
  });

  it("does not qualify a study that has not reached a verdict", () => {
    // A qualifier on a running study would describe a verdict that does not exist yet.
    render(<ValidationStudyOutcome state="acquiring_processed" confidence={null} route="deposit" />);
    expect(screen.queryByText("Deposited data")).not.toBeInTheDocument();
  });
});
