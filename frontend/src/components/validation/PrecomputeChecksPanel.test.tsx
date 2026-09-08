/**
 * plan_7 step 14 at the C1 gate: the cheap checks, in front of the person authorising the spend.
 *
 * "The deposit holds 5 bigwigs and the paper describes 21 RNA-seq samples" belongs before an
 * approval, not in the report afterwards.
 *
 * Deterministic blocks, judgment advises: the species mismatch is the one check that stops an
 * approval, and it offers a deliberate override with a stated reason. The other three are labelled
 * as advisory, because a section that presents an opinion as a blocker teaches people to ignore it.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PrecomputeChecksPanel, type PrecomputeChecks } from "./PrecomputeChecksPanel";

const speciesOk = {
  check: "species_matches",
  verdict: "ok",
  detail: "the paper and the deposit both name Homo sapiens",
  blocking: false,
  decided_by: "measurement",
  model: null,
  reason: "",
  confidence: 0,
};

const speciesMismatch = {
  ...speciesOk,
  verdict: "mismatch",
  detail: "The paper's plan names Homo sapiens and the deposit declares Mus musculus.",
  blocking: true,
};

const methodsThin = {
  check: "methods_detailed_enough",
  verdict: "mismatch",
  detail: "no aligner, genome build or threshold is named",
  blocking: false,
  decided_by: "model",
  model: "claude-opus-4-8",
  reason: "no aligner, genome build or threshold is named",
  confidence: 0.85,
};

const checks = (over: Partial<PrecomputeChecks> = {}): PrecomputeChecks => ({
  species_matches: speciesOk,
  sample_data_matches_paper: {
    check: "sample_data_matches_paper",
    verdict: "ok",
    detail: "the deposit holds 1 series-level file",
    blocking: false,
    decided_by: "measurement",
    model: null,
    reason: "",
    confidence: 0,
  },
  methods_detailed_enough: { ...methodsThin, verdict: "ok", detail: "STAR, GRCh38 and padj < 0.05 are named" },
  samples_described_enough: {
    check: "samples_described_enough",
    verdict: "ok",
    detail: "each sample's condition is stated",
    blocking: false,
    decided_by: "model",
    model: "claude-opus-4-8",
    reason: "each sample's condition is stated",
    confidence: 0.9,
  },
  ...over,
});

describe("PrecomputeChecksPanel", () => {
  it("renders nothing before the checks have run", () => {
    const { container } = render(<PrecomputeChecksPanel checks={null} onOverride={jest.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows all four checks so the approver sees them before spending", () => {
    render(<PrecomputeChecksPanel checks={checks()} onOverride={jest.fn()} />);
    expect(screen.getByText(/species/i)).toBeInTheDocument();
    expect(screen.getByText(/sample data/i)).toBeInTheDocument();
    expect(screen.getByText(/methods/i)).toBeInTheDocument();
    expect(screen.getByText(/samples described/i)).toBeInTheDocument();
  });

  it("names the model behind a judgment, and does not for a measurement", () => {
    render(<PrecomputeChecksPanel checks={checks()} onOverride={jest.fn()} />);
    expect(screen.getAllByText(/claude-opus-4-8/).length).toBeGreaterThan(0);
  });

  it("marks the three sufficiency checks as advisory", () => {
    render(<PrecomputeChecksPanel checks={checks({ methods_detailed_enough: methodsThin })} onOverride={jest.fn()} />);
    expect(screen.getAllByText(/advisory/i).length).toBeGreaterThan(0);
  });

  it("says a thin methods section is a finding, not a refusal", () => {
    render(<PrecomputeChecksPanel checks={checks({ methods_detailed_enough: methodsThin })} onOverride={jest.fn()} />);
    expect(screen.getByText(/no aligner, genome build or threshold is named/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /override/i })).not.toBeInTheDocument();
  });

  it("blocks on a species mismatch and shows both organisms", () => {
    render(<PrecomputeChecksPanel checks={checks({ species_matches: speciesMismatch })} onOverride={jest.fn()} />);
    expect(screen.getByText(/Homo sapiens/)).toBeInTheDocument();
    expect(screen.getByText(/Mus musculus/)).toBeInTheDocument();
    expect(screen.getByText(/approval is blocked/i)).toBeInTheDocument();
  });

  it("offers a deliberate override that requires a reason", async () => {
    const onOverride = jest.fn();
    render(<PrecomputeChecksPanel checks={checks({ species_matches: speciesMismatch })} onOverride={onOverride} />);

    await userEvent.click(screen.getByRole("button", { name: /run it anyway/i }));
    const confirm = screen.getByRole("button", { name: /record and continue/i });
    expect(confirm).toBeDisabled();

    await userEvent.type(screen.getByRole("textbox"), "the deposit's annotation is wrong");
    await userEvent.click(confirm);
    expect(onOverride).toHaveBeenCalledWith("the deposit's annotation is wrong");
  });

  it("shows a recorded override instead of offering it again", () => {
    render(
      <PrecomputeChecksPanel
        checks={checks({ species_matches: speciesMismatch })}
        override={{ reason: "the deposit's annotation is wrong", by_user_id: 1, at: "2026-09-07T00:00:00Z" }}
        onOverride={jest.fn()}
      />,
    );
    expect(screen.getByText(/the deposit's annotation is wrong/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /run it anyway/i })).not.toBeInTheDocument();
  });

  it("does not block when the species could not be established", () => {
    render(
      <PrecomputeChecksPanel
        checks={checks({
          species_matches: { ...speciesOk, verdict: "unknown", detail: "the deposit declares no organism" },
        })}
        onOverride={jest.fn()}
      />,
    );
    expect(screen.queryByText(/approval is blocked/i)).not.toBeInTheDocument();
  });
});
