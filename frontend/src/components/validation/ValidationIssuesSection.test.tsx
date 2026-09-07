/**
 * plan_7 step 14c: the steps that hit an error while validating this paper.
 *
 * A refusal used to be invisible. A model that declined every claim binding produced a plan that had
 * silently fallen back to the alias table, with nothing on screen to say so.
 *
 * Informational, never alarming: the section is absent when nothing went wrong, and every row states
 * whether the step carried on with a fallback or produced nothing. Without that distinction the
 * section cries wolf and users learn to ignore it.
 */
import { render, screen } from "@testing-library/react";

import { ValidationIssuesSection } from "./ValidationIssuesSection";

const issue = (over: Partial<Parameters<typeof ValidationIssuesSection>[0]["issues"][number]> = {}) => ({
  step: "binding the paper's claims to measurable metrics",
  outcome: "refusal",
  impact: "degraded",
  message: "The model claude-opus-4-8 declined to answer while binding the paper's claims.",
  model: "claude-opus-4-8",
  at: "2026-09-07T12:00:00+00:00",
  ...over,
});

describe("ValidationIssuesSection", () => {
  it("renders nothing when every step got its answer", () => {
    const { container } = render(<ValidationIssuesSection issues={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("says plainly that some steps may have affected this validation", () => {
    render(<ValidationIssuesSection issues={[issue()]} />);
    expect(
      screen.getByText(/errors that may affect the ability to validate this paper/i),
    ).toBeInTheDocument();
  });

  it("names the step in the user's language, not the function name", () => {
    render(<ValidationIssuesSection issues={[issue()]} />);
    expect(screen.getByText(/binding the paper's claims to measurable metrics/)).toBeInTheDocument();
  });

  it("names the model a refusal needs an account exception for", () => {
    render(<ValidationIssuesSection issues={[issue()]} />);
    expect(screen.getAllByText(/claude-opus-4-8/).length).toBeGreaterThan(0);
  });

  it("distinguishes a step that carried on from one that produced nothing", () => {
    render(
      <ValidationIssuesSection
        issues={[issue(), issue({ step: "ratifying the measured verdict", impact: "blocked" })]}
      />,
    );
    expect(screen.getByText(/continued with a fallback/i)).toBeInTheDocument();
    expect(screen.getByText(/produced nothing/i)).toBeInTheDocument();
  });

  it("says what happened in words rather than showing the raw token", () => {
    render(<ValidationIssuesSection issues={[issue({ outcome: "unreachable" })]} />);
    expect(screen.getByText(/could not reach the language model/i)).toBeInTheDocument();
    expect(screen.queryByText("unreachable")).not.toBeInTheDocument();
  });

  it("shows one row per occurrence, including the same step twice", () => {
    render(<ValidationIssuesSection issues={[issue(), issue()]} />);
    expect(screen.getAllByText(/binding the paper's claims to measurable metrics/)).toHaveLength(2);
  });

  it("renders a row with no model without inventing one", () => {
    render(<ValidationIssuesSection issues={[issue({ model: null, outcome: "internal" })]} />);
    expect(screen.getByText(/internal error/i)).toBeInTheDocument();
    expect(screen.queryByText(/null/)).not.toBeInTheDocument();
  });
});
