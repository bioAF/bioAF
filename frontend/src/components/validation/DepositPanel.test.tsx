/**
 * plan_7 step 15: the rest of the C1 gate, which step 10 only half built.
 *
 * The route modal shipped; the deposit inventory, the assisted picker, the inspection numbers and
 * the metadata association table did not. Without the picker, `assisted` is not a working mode: the
 * driver stores the inventory and waits for a person who has no control to answer with.
 *
 * Follows `SampleManifestPicker`, which already does recognisable-row picking. This is the same
 * interaction over a different list.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DepositPanel, type DepositEvidence } from "./DepositPanel";

const inventory = {
  accession: "GSE274331",
  source: "filelist+directory",
  listed_at: "2026-09-07T00:00:00Z",
  entries: [
    {
      filename: "GSE274331_TPMs.xlsx",
      url: "https://ftp.ncbi.nlm.nih.gov/x/GSE274331_TPMs.xlsx",
      classification: "matrix_normalized",
      level: "series",
      gsm: null,
      size_bytes: 2_400_000,
      deposited_type: null,
    },
    {
      filename: "GSE274331_meta.tsv",
      url: "https://ftp.ncbi.nlm.nih.gov/x/GSE274331_meta.tsv",
      classification: "metadata",
      level: "series",
      gsm: null,
      size_bytes: 4096,
      deposited_type: null,
    },
    {
      filename: "GSM1_signal.bigwig",
      url: "https://ftp.ncbi.nlm.nih.gov/x/GSM1_signal.bigwig",
      classification: "coverage",
      level: "sample",
      gsm: "GSM1",
      size_bytes: 900_000_000,
      deposited_type: "BIGWIG",
    },
  ],
  triplets: [],
};

const selection = {
  primary_matrix: "GSE274331_TPMs.xlsx",
  matrix_files: ["GSE274331_TPMs.xlsx"],
  metadata_file: "GSE274331_meta.tsv",
  value_type: "tpm",
  reason: "the only per-gene matrix in the deposit",
  confidence: 0.92,
  declined: false,
  decided_by: "model",
  model: "claude-opus-4-8",
};

const evidence = (over: Partial<DepositEvidence> = {}): DepositEvidence => ({
  deposit_inventory: inventory,
  deposit_selection: selection,
  deposit_inspection: null,
  deposit_metadata_association: null,
  deposit_failed: null,
  ...over,
});

describe("DepositPanel", () => {
  it("renders nothing before the deposit has been listed", () => {
    const { container } = render(
      <DepositPanel evidence={{}} canPick={false} onPick={jest.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lists what the study deposited, grouped by series and per sample", () => {
    render(<DepositPanel evidence={evidence()} canPick={false} onPick={jest.fn()} />);
    expect(screen.getAllByText("GSE274331_TPMs.xlsx").length).toBeGreaterThan(0);
    expect(screen.getByText("GSM1_signal.bigwig")).toBeInTheDocument();
    expect(screen.getAllByText(/per sample/i).length).toBeGreaterThan(0);
  });

  it("shows the model's pick with its reason and confidence", () => {
    render(<DepositPanel evidence={evidence()} canPick={false} onPick={jest.fn()} />);
    expect(screen.getByText(/the only per-gene matrix in the deposit/)).toBeInTheDocument();
    expect(screen.getByText(/0\.92/)).toBeInTheDocument();
    expect(screen.getByText(/claude-opus-4-8/)).toBeInTheDocument();
  });

  it("says a size in something a person reads", () => {
    render(<DepositPanel evidence={evidence()} canPick={false} onPick={jest.fn()} />);
    expect(screen.getByText(/2\.3 MB/)).toBeInTheDocument();
  });

  it("offers no picker when a person cannot pick", () => {
    render(<DepositPanel evidence={evidence()} canPick={false} onPick={jest.fn()} />);
    expect(screen.queryByRole("button", { name: /use this file/i })).not.toBeInTheDocument();
  });

  it("lets a person choose the matrix in assisted mode", async () => {
    const onPick = jest.fn();
    render(
      <DepositPanel
        evidence={evidence({ deposit_selection: null })}
        canPick
        onPick={onPick}
      />,
    );

    await userEvent.selectOptions(screen.getByLabelText(/matrix to reproduce from/i), "GSE274331_TPMs.xlsx");
    await userEvent.click(screen.getByRole("button", { name: /use this file/i }));

    expect(onPick).toHaveBeenCalledWith(
      expect.objectContaining({ primary_matrix: "GSE274331_TPMs.xlsx", decided_by: "human" }),
    );
  });

  it("does not offer a file that cannot serve as a reproduction input", () => {
    render(<DepositPanel evidence={evidence({ deposit_selection: null })} canPick onPick={jest.fn()} />);
    const select = screen.getByLabelText(/matrix to reproduce from/i);
    expect(within(select).queryByText("GSM1_signal.bigwig")).not.toBeInTheDocument();
  });

  it("shows why the deposit could not be taken, when it could not", () => {
    render(
      <DepositPanel
        evidence={evidence({
          deposit_failed: { reason: "GSE274331_TPMs.xlsx could not be downloaded from GEO", at: "2026-09-07" },
        })}
        canPick={false}
        onPick={jest.fn()}
      />,
    );
    expect(screen.getByText(/could not be downloaded from GEO/)).toBeInTheDocument();
  });

  it("shows what the matrix was measured to be, and that it overrules the model's guess", () => {
    render(
      <DepositPanel
        evidence={evidence({
          deposit_inspection: {
            value_type_observed: "tpm_or_cpm",
            value_type_claimed: "tpm",
            value_type_disagrees: false,
            n_rows: 37248,
            n_columns: 3,
            columns: ["CTRL_1", "CTRL_2", "KD_1"],
            usable: true,
            unusable_reason: null,
            library_size_ratio: 1.0,
          },
        })}
        canPick={false}
        onPick={jest.fn()}
      />,
    );
    expect(screen.getByText(/37,248/)).toBeInTheDocument();
    expect(screen.getByText(/tpm_or_cpm/)).toBeInTheDocument();
    expect(screen.getAllByText(/measured/i).length).toBeGreaterThan(0);
  });

  it("shows where each column's condition came from", () => {
    render(
      <DepositPanel
        evidence={evidence({
          deposit_metadata_association: [
            { column: "CTRL_1", condition: "Control", source: "metadata_file", confidence: 1.0, reason: "" },
            { column: "KD_1", condition: "KD", source: "column_name", confidence: 0.7, reason: "" },
          ],
        })}
        canPick={false}
        onPick={jest.fn()}
      />,
    );
    expect(screen.getByText("CTRL_1")).toBeInTheDocument();
    expect(screen.getByText(/metadata file/i)).toBeInTheDocument();
    expect(screen.getByText(/column name/i)).toBeInTheDocument();
  });
});
