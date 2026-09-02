/**
 * plan_6 step 5: the C1 gate shows what the model decided.
 *
 * Rendered in BOTH autonomy modes. A scientist authorising a run has to be able to see which model
 * bound which claim, on what reasoning, and how sure it was, because the output of this feature is
 * informational and that claim is only honest if the decisions are attributable.
 */
import { render, screen } from "@testing-library/react";

import { AiDecisionList } from "./AiDecisionList";

const decision = (over: Partial<Parameters<typeof AiDecisionList>[0]["decisions"][number]> = {}) => ({
  metric_key: "samd1_chip_peaks",
  bound_key: "peak_count",
  resolved: true,
  reason: "the paper's headline peak number",
  confidence: 0.94,
  model: "claude-opus-4-8",
  decided_by: "model",
  low_confidence: false,
  ...over,
});

describe("AiDecisionList", () => {
  it("renders nothing when there are no decisions", () => {
    const { container } = render(<AiDecisionList decisions={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names the model that decided", () => {
    render(<AiDecisionList decisions={[decision()]} />);
    expect(screen.getByText(/claude-opus-4-8/)).toBeInTheDocument();
  });

  it("shows what each claim was bound to, and why", () => {
    render(<AiDecisionList decisions={[decision()]} />);
    expect(screen.getByText("samd1_chip_peaks")).toBeInTheDocument();
    expect(screen.getByText("peak_count")).toBeInTheDocument();
    expect(screen.getByText(/the paper's headline peak number/)).toBeInTheDocument();
  });

  it("shows the confidence for every row", () => {
    render(<AiDecisionList decisions={[decision()]} />);
    expect(screen.getByText("0.94")).toBeInTheDocument();
  });

  it("marks a low-confidence row so it is read first", () => {
    render(<AiDecisionList decisions={[decision({ confidence: 0.61, low_confidence: true })]} />);
    expect(screen.getByText(/low confidence/i)).toBeInTheDocument();
  });

  it("counts how many claims were resolved", () => {
    render(
      <AiDecisionList
        decisions={[decision(), decision({ metric_key: "deg_count", bound_key: null, resolved: false })]}
      />,
    );
    expect(screen.getByText(/1 of 2/)).toBeInTheDocument();
  });

  it("says when a claim was declined rather than leaving it blank", () => {
    render(
      <AiDecisionList
        decisions={[
          decision({
            metric_key: "deg_count",
            bound_key: null,
            resolved: false,
            reason: "a DE gene count is not a controlled metric",
          }),
        ]}
      />,
    );
    expect(screen.getByText(/declined/i)).toBeInTheDocument();
    expect(screen.getByText(/a DE gene count is not a controlled metric/)).toBeInTheDocument();
  });

  it("does not present an alias-table lookup as a model decision", () => {
    render(
      <AiDecisionList
        decisions={[
          decision({ decided_by: "alias_table", model: null, confidence: null, reason: null }),
        ]}
      />,
    );
    expect(screen.queryByText(/claude-opus-4-8/)).not.toBeInTheDocument();
    expect(screen.getByText(/alias table/i)).toBeInTheDocument();
  });
});
