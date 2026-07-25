import { render, screen } from "@testing-library/react";
import { Level3ResultPanel } from "./Level3ResultPanel";

const AGREE = {
  concordance: {
    kind: "gene",
    verdict: "agree",
    paper_n: 100,
    our_n: 90,
    overlap: 85,
    concordant: 82,
    directional_overlap_frac: 0.82,
    enrichment_p: 1e-30,
    notes: [],
  },
  our_finding_set: { n_sig: 90, namespace: "symbol", n_up: 50, n_down: 40, parse_notes: [], entities: [] },
};

test("renders the concordance verdict and the two set sizes", () => {
  render(<Level3ResultPanel result={AGREE} contrast="dex vs untreated" />);
  expect(screen.getByText(/finding reproduced/i)).toBeInTheDocument(); // agree verdict badge
  expect(screen.getByText(/dex vs untreated/)).toBeInTheDocument();
  // paper set (100) and our set (90) both surfaced as their own stats
  expect(screen.getByText("100")).toBeInTheDocument();
  expect(screen.getByText("90")).toBeInTheDocument();
});

test("renders directional overlap as a percentage and the enrichment p-value", () => {
  render(<Level3ResultPanel result={AGREE} />);
  expect(screen.getByText(/82%/)).toBeInTheDocument();
  // enrichment p rendered in scientific notation
  expect(screen.getByText(/1e-30|1\.0e-30/i)).toBeInTheDocument();
});

test("a divergence reads as not reproduced", () => {
  const diverge = { ...AGREE, concordance: { ...AGREE.concordance, verdict: "diverge" } };
  render(<Level3ResultPanel result={diverge} />);
  expect(screen.getByText(/did not reproduce/i)).toBeInTheDocument();
});

test("not_computed surfaces its notes and does not claim a verdict", () => {
  const nc = {
    concordance: {
      kind: "gene",
      verdict: "not_computed",
      paper_n: 100,
      our_n: 0,
      overlap: 0,
      concordant: 0,
      directional_overlap_frac: 0,
      enrichment_p: 1,
      notes: ["namespace mismatch: paper=symbol ours=ensembl_gene"],
    },
  };
  render(<Level3ResultPanel result={nc} />);
  expect(screen.getByText(/not computed/i)).toBeInTheDocument();
  expect(screen.getByText(/namespace mismatch/i)).toBeInTheDocument();
});

test("renders nothing when there is no concordance", () => {
  const { container } = render(<Level3ResultPanel result={null} />);
  expect(container).toBeEmptyDOMElement();
});
