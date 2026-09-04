import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Level3Gate } from "./Level3Gate";

jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => true, roleName: "admin", loading: false, permissions: new Set() }),
}));

jest.mock("@/lib/api", () => ({
  api: { put: jest.fn(), post: jest.fn(), get: jest.fn() },
}));

import { api } from "@/lib/api";

const mockPut = api.put as jest.Mock;
const mockPost = api.post as jest.Mock;
const mockGet = api.get as jest.Mock;

const DESIGN = {
  contrasts: [
    {
      name: "dex vs untreated",
      test_condition: "dex",
      reference_condition: "untreated",
      test_samples: ["SRX1", "SRX2"],
      reference_samples: ["SRX3", "SRX4"],
    },
  ],
  thresholds: { log2fc: 1.0, padj: 0.05 },
};

beforeEach(() => {
  mockPut.mockReset();
  mockPost.mockReset();
  mockGet.mockReset();
  mockPut.mockResolvedValue({ id: 1, state: "plan_ready" });
  mockPost.mockResolvedValue({ id: 1, state: "plan_ready" });
  mockGet.mockResolvedValue({ candidates: [] });
});

test("renders the extracted differential design for review", async () => {
  render(<Level3Gate studyId={1} design={DESIGN} claim={null} onChanged={jest.fn()} />);
  // no manifest here (default mock returns candidates only) -> free-text sample inputs
  expect(screen.getByDisplayValue("dex vs untreated")).toBeInTheDocument();
  expect(screen.getByDisplayValue("SRX1, SRX2")).toBeInTheDocument();
  expect(screen.getByDisplayValue("SRX3, SRX4")).toBeInTheDocument();
  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/validation-studies/1/sample-manifest"));
});

test("saving an edited design PUTs the normalized contrast to the design endpoint", async () => {
  const onChanged = jest.fn();
  render(<Level3Gate studyId={7} design={DESIGN} claim={null} onChanged={onChanged} />);

  const testInput = screen.getByLabelText(/test samples/i);
  await userEvent.clear(testInput);
  await userEvent.type(testInput, "SRX30659361, SRX30659364");
  await userEvent.click(screen.getByRole("button", { name: /save design/i }));

  await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
  const [url, body] = mockPut.mock.calls[0];
  expect(url).toBe("/api/validation-studies/7/differential-design");
  expect(body.contrasts[0].test_samples).toEqual(["SRX30659361", "SRX30659364"]);
  expect(body.contrasts[0].reference_samples).toEqual(["SRX3", "SRX4"]);
  expect(body.thresholds).toEqual({ log2fc: 1.0, padj: 0.05 });
  expect(onChanged).toHaveBeenCalled();
});

test("a matched-pairs pairing is parsed into the subjects map on save", async () => {
  render(<Level3Gate studyId={7} design={DESIGN} claim={null} onChanged={jest.fn()} />);

  await userEvent.type(
    screen.getByLabelText(/subject pairing/i),
    "SRX1=donorA\nSRX2=donorB\nSRX3=donorA\nSRX4=donorB",
  );
  await userEvent.click(screen.getByRole("button", { name: /save design/i }));

  await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
  const [, body] = mockPut.mock.calls[0];
  expect(body.contrasts[0].subjects).toEqual({
    SRX1: "donorA",
    SRX2: "donorB",
    SRX3: "donorA",
    SRX4: "donorB",
  });
});

test("an already-saved pairing pre-fills the subject-pairing field", async () => {
  const paired = {
    ...DESIGN,
    contrasts: [{ ...DESIGN.contrasts[0], subjects: { SRX1: "donorA", SRX3: "donorA" } }],
  };
  render(<Level3Gate studyId={1} design={paired} claim={null} onChanged={jest.fn()} />);
  expect((screen.getByLabelText(/subject pairing/i) as HTMLTextAreaElement).value).toContain("SRX1=donorA");
  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/validation-studies/1/sample-manifest"));
});

// ---- Level-3 sample picker (recognition over accession typing) ----

const MANIFEST = {
  samples: [
    {
      experiment_accession: "SRX1",
      run_accession: "SRR1",
      sample_accession: "SRS1",
      title: "Dexamethasone rep 1",
      condition: "treatment: dex",
    },
    {
      experiment_accession: "SRX3",
      run_accession: "SRR3",
      sample_accession: "SRS3",
      title: "Untreated rep 1",
      condition: "treatment: untreated",
    },
  ],
  unavailable_reason: null,
};

// Route the manifest GET to a manifest payload; everything else keeps the default candidates shape.
function mockManifest(payload: unknown) {
  mockGet.mockImplementation(async (url: string) =>
    url.includes("sample-manifest") ? payload : { candidates: [] },
  );
}

test("renders recognizable sample rows (title + condition) instead of free-text sample inputs", async () => {
  mockManifest(MANIFEST);
  render(<Level3Gate studyId={7} design={DESIGN} claim={null} onChanged={jest.fn()} />);

  expect(await screen.findByText("Dexamethasone rep 1")).toBeInTheDocument();
  expect(screen.getByText("Untreated rep 1")).toBeInTheDocument();
  expect(screen.getByText(/treatment: dex/i)).toBeInTheDocument();
  // the scientist never sees the blind free-text accession boxes when a manifest is available
  expect(screen.queryByLabelText(/^test samples/i)).not.toBeInTheDocument();
});

test("pre-groups samples by the extracted design and saves experiment accessions to the right arms", async () => {
  mockManifest(MANIFEST);
  render(<Level3Gate studyId={7} design={DESIGN} claim={null} onChanged={jest.fn()} />);
  await screen.findByText("Dexamethasone rep 1");

  await userEvent.click(screen.getByRole("button", { name: /save design/i }));

  await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
  const [url, body] = mockPut.mock.calls[0];
  expect(url).toBe("/api/validation-studies/7/differential-design");
  // SRX1 is in the design's test_samples, SRX3 in reference_samples -> pre-grouped, no typing
  expect(body.contrasts[0].test_samples).toEqual(["SRX1"]);
  expect(body.contrasts[0].reference_samples).toEqual(["SRX3"]);
});

test("reassigning a sample moves it to the other arm on save", async () => {
  mockManifest(MANIFEST);
  render(<Level3Gate studyId={7} design={DESIGN} claim={null} onChanged={jest.fn()} />);
  await screen.findByText("Dexamethasone rep 1");

  await userEvent.selectOptions(screen.getByLabelText(/arm for Dexamethasone rep 1/i), "reference");
  await userEvent.click(screen.getByRole("button", { name: /save design/i }));

  await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
  const [, body] = mockPut.mock.calls[0];
  expect(body.contrasts[0].test_samples).toEqual([]);
  expect(body.contrasts[0].reference_samples).toEqual(expect.arrayContaining(["SRX1", "SRX3"]));
});

test("manual-add injects a sample id the manifest missed into the saved design", async () => {
  mockManifest(MANIFEST);
  render(<Level3Gate studyId={7} design={DESIGN} claim={null} onChanged={jest.fn()} />);
  await screen.findByText("Dexamethasone rep 1");

  await userEvent.type(screen.getByLabelText(/add a sample/i), "SRX_EXTRA");
  await userEvent.click(screen.getByRole("button", { name: /add sample/i }));
  await userEvent.click(screen.getByRole("button", { name: /save design/i }));

  await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
  const [, body] = mockPut.mock.calls[0];
  expect(body.contrasts[0].test_samples).toContain("SRX_EXTRA");
});

test("a per-sample subject is saved into the subjects map", async () => {
  mockManifest(MANIFEST);
  render(<Level3Gate studyId={7} design={DESIGN} claim={null} onChanged={jest.fn()} />);
  await screen.findByText("Dexamethasone rep 1");

  await userEvent.type(screen.getByLabelText(/subject for Dexamethasone rep 1/i), "donorA");
  await userEvent.type(screen.getByLabelText(/subject for Untreated rep 1/i), "donorA");
  await userEvent.click(screen.getByRole("button", { name: /save design/i }));

  await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
  const [, body] = mockPut.mock.calls[0];
  expect(body.contrasts[0].subjects).toEqual({ SRX1: "donorA", SRX3: "donorA" });
});

test("an unavailable manifest falls back to the free-text sample inputs", async () => {
  mockManifest({ samples: [], unavailable_reason: "This study has no deposited accession to list samples from." });
  render(<Level3Gate studyId={7} design={DESIGN} claim={null} onChanged={jest.fn()} />);

  expect(await screen.findByText(/sample list unavailable/i)).toBeInTheDocument();
  // free-text entry still works
  const testInput = screen.getByLabelText(/^test samples/i);
  expect(testInput).toBeInTheDocument();
  await userEvent.clear(testInput);
  await userEvent.type(testInput, "SRX9");
  await userEvent.click(screen.getByRole("button", { name: /save design/i }));

  await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
  const [, body] = mockPut.mock.calls[0];
  expect(body.contrasts[0].test_samples).toEqual(["SRX9"]);
});

test("confirming a pasted ground-truth table POSTs to the finding-set endpoint", async () => {
  const onChanged = jest.fn();
  render(<Level3Gate studyId={7} design={DESIGN} claim={null} onChanged={onChanged} />);

  await userEvent.type(screen.getByLabelText(/result table/i), "gene,log2FoldChange,padj\nA1BG,2.5,0.001");
  await userEvent.type(screen.getByLabelText(/source/i), "Table S3");
  await userEvent.click(screen.getByRole("button", { name: /confirm ground-truth set/i }));

  await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
  const [url, body] = mockPost.mock.calls[0];
  expect(url).toBe("/api/validation-studies/7/finding-set");
  expect(body.kind).toBe("gene");
  expect(body.table_text).toContain("A1BG");
  expect(body.source_locator).toBe("Table S3");
  expect(onChanged).toHaveBeenCalled();
});

test("auto-fetch pre-fills the table from a GEO candidate", async () => {
  mockGet.mockResolvedValue({
    candidates: [
      {
        filename: "GSE1_DEG.csv",
        source: "geo_supplementary",
        n_sig: 5,
        table_text: "gene,log2FoldChange,padj\nA1BG,2.5,0.001",
      },
    ],
  });
  render(<Level3Gate studyId={7} design={DESIGN} claim={null} onChanged={jest.fn()} />);

  await userEvent.click(screen.getByRole("button", { name: /auto-fetch/i }));

  await waitFor(() =>
    expect(mockGet).toHaveBeenCalledWith("/api/validation-studies/7/finding-set/candidates?kind=gene"),
  );
  const textarea = screen.getByLabelText(/result table/i) as HTMLTextAreaElement;
  expect(textarea.value).toContain("A1BG");
  expect((screen.getByLabelText(/source/i) as HTMLInputElement).value).toBe("GSE1_DEG.csv");
});

test("auto-fetch with no candidates points the user to the journal SI", async () => {
  mockGet.mockResolvedValue({ candidates: [] });
  render(<Level3Gate studyId={7} design={DESIGN} claim={null} onChanged={jest.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: /auto-fetch/i }));
  await waitFor(() => expect(screen.getByText(/no deposited result table/i)).toBeInTheDocument());
});

test("shows the parsed finding-set summary once a claim is confirmed", async () => {
  const claim = {
    kind: "gene",
    namespace: "symbol",
    confirmed: true,
    source_locator: "Table S3",
    thresholds: { log2fc: 1.0, padj: 0.05 },
    finding_set: { n_sig: 10, n_up: 6, n_down: 4, namespace: "symbol", parse_notes: [], entities: [] },
  };
  render(<Level3Gate studyId={1} design={DESIGN} claim={claim} onChanged={jest.fn()} />);
  expect(screen.getByText(/10/)).toBeInTheDocument();
  expect(screen.getByText(/confirmed/i)).toBeInTheDocument();
  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/api/validation-studies/1/sample-manifest"));
});

// --- capability declaration (plan_0 step 6) ---

test("offers only the finding kind the study's pipeline can actually reproduce", async () => {
  render(
    <Level3Gate
      studyId={1}
      design={DESIGN}
      claim={null}
      supportedFindingKinds={["interval"]}
      onChanged={jest.fn()}
    />,
  );
  const select = screen.getByLabelText(/result set kind/i) as HTMLSelectElement;
  const options = Array.from(select.options).map((o) => o.value);
  expect(options).toEqual(["interval"]);
});

test("offers gene for an scRNA-seq study", async () => {
  render(
    <Level3Gate studyId={1} design={DESIGN} claim={null} supportedFindingKinds={["gene"]} onChanged={jest.fn()} />,
  );
  const select = screen.getByLabelText(/result set kind/i) as HTMLSelectElement;
  expect(Array.from(select.options).map((o) => o.value)).toEqual(["gene"]);
});

test("states plainly that a pipeline with no Level-3 route cannot reproduce a finding", async () => {
  // Hiding the section would leave a scientist wondering where the control went; saying why is the
  // point of declaring the capability at all.
  render(<Level3Gate studyId={1} design={DESIGN} claim={null} supportedFindingKinds={[]} onChanged={jest.fn()} />);
  expect(screen.getByText(/cannot reproduce a reported finding/i)).toBeInTheDocument();
  expect(screen.queryByLabelText(/result set kind/i)).not.toBeInTheDocument();
});

test("an older response with no declaration degrades to offering both kinds", async () => {
  // A study whose plan was rendered before the field existed must not throw or render an empty gate.
  render(<Level3Gate studyId={1} design={DESIGN} claim={null} onChanged={jest.fn()} />);
  const select = screen.getByLabelText(/result set kind/i) as HTMLSelectElement;
  expect(Array.from(select.options).map((o) => o.value)).toEqual(["gene", "interval"]);
});

// ---- the column picker: what the gate asks when a table's header is not recognised ----
//
// A real csaw deposit names its coordinates regions.seqnames / regions.start / regions.end, which
// the alias list does not know, so the table parsed to nothing and the gate said "could not locate
// chrom/start/end columns". That is not a question a scientist can answer. This is: here is your
// header, which column is the chromosome?

const NEEDS_MAPPING = {
  kind: "interval",
  namespace: "interval",
  confirmed: true,
  finding_set: { n_sig: 0, n_up: 0, n_down: 0, namespace: "interval", parse_notes: ["could not locate chrom/start/end columns"], entities: [] },
  needs_column_mapping: {
    header: ["", "regions.seqnames", "regions.start", "regions.end", "combined.FDR", "combined.rep.logFC"],
    roles: ["chrom", "start", "end", "lfc", "padj", "pval"],
  },
};

test("asks which column plays each role when the header is not recognised", async () => {
  render(<Level3Gate studyId={1} design={DESIGN} claim={NEEDS_MAPPING as never} onChanged={jest.fn()} />);

  expect(await screen.findByText(/which column/i)).toBeInTheDocument();
  const chrom = screen.getByLabelText(/chrom column/i) as HTMLSelectElement;
  expect(Array.from(chrom.options).map((o) => o.value)).toEqual(
    expect.arrayContaining(["regions.seqnames", "regions.start", "combined.FDR"]),
  );
});

test("posts the chosen mapping back with the table", async () => {
  render(<Level3Gate studyId={1} design={DESIGN} claim={NEEDS_MAPPING as never} onChanged={jest.fn()} />);

  await userEvent.selectOptions(await screen.findByLabelText(/chrom column/i), "regions.seqnames");
  await userEvent.selectOptions(screen.getByLabelText(/start column/i), "regions.start");
  await userEvent.selectOptions(screen.getByLabelText(/end column/i), "regions.end");
  await userEvent.click(screen.getByRole("button", { name: /use these columns/i }));

  await waitFor(() => {
    const call = mockPost.mock.calls.find((c) => String(c[0]).includes("/finding-set"));
    expect(call?.[1].column_map).toEqual({
      chrom: "regions.seqnames",
      start: "regions.start",
      end: "regions.end",
    });
  });
});

test("does not ask when the table parsed", () => {
  const ok = {
    kind: "interval",
    namespace: "interval",
    confirmed: true,
    finding_set: { n_sig: 2, n_up: 1, n_down: 1, namespace: "interval", parse_notes: [], entities: [{ id: "1:100-200" }] },
  };
  render(<Level3Gate studyId={1} design={DESIGN} claim={ok as never} onChanged={jest.fn()} />);
  expect(screen.queryByText(/which column/i)).not.toBeInTheDocument();
});

test("shows what the model chose when it resolved the columns itself", async () => {
  const resolved = {
    kind: "interval",
    namespace: "interval",
    confirmed: true,
    finding_set: { n_sig: 2, n_up: 1, n_down: 1, namespace: "interval", parse_notes: [], entities: [{ id: "1:100-200" }] },
    column_mapping: {
      columns: { chrom: "regions.seqnames", start: "regions.start" },
      decided_by: "model",
      model: "claude-opus-4-8",
      confidence: 0.96,
      reason: "csaw prefixes its columns",
    },
  };
  render(<Level3Gate studyId={1} design={DESIGN} claim={resolved as never} onChanged={jest.fn()} />);

  expect(await screen.findByText(/claude-opus-4-8/)).toBeInTheDocument();
  expect(screen.getByText(/regions\.seqnames/)).toBeInTheDocument();
});

// ---- the contrast this run reproduces, not whichever was listed first ----

const MULTI = {
  contrasts: [
    { name: "Nkx2.2 KO vs control (RNA-seq)", assay: "RNA-seq", test_samples: [], reference_samples: [],
      thresholds: { log2fc: 1.0, padj: 0.05 } },
    { name: "NKX2.2 differential binding (ChIP-seq)", assay: "ChIP-seq", test_samples: [], reference_samples: [],
      thresholds: { log2fc: null, padj: 0.05 } },
  ],
  thresholds: { log2fc: 1.0, padj: 0.05 },
  selected_contrast: { contrast_index: 1, decided_by: "model", model: "claude-opus-4-8", confidence: 0.95,
                       reason: "the only ChIP-seq contrast" },
};

test("edits the selected contrast, not the first one", async () => {
  render(<Level3Gate studyId={1} design={MULTI as never} claim={null} onChanged={jest.fn()} />);
  const name = (await screen.findByLabelText(/contrast name/i)) as HTMLInputElement;
  expect(name.value).toBe("NKX2.2 differential binding (ChIP-seq)");
});

test("uses the selected contrast's own thresholds", async () => {
  render(<Level3Gate studyId={1} design={MULTI as never} claim={null} onChanged={jest.fn()} />);
  const padj = (await screen.findByLabelText(/padj threshold/i)) as HTMLInputElement;
  expect(padj.value).toBe("0.05");
  const lfc = screen.getByLabelText(/log2fc threshold/i) as HTMLInputElement;
  expect(lfc.value).toBe("");
});

test("says which contrast was chosen and why", async () => {
  render(<Level3Gate studyId={1} design={MULTI as never} claim={null} onChanged={jest.fn()} />);
  expect(await screen.findByText(/claude-opus-4-8/)).toBeInTheDocument();
  expect(screen.getByText(/only ChIP-seq contrast/i)).toBeInTheDocument();
});

test("lets a person choose a different contrast", async () => {
  render(<Level3Gate studyId={1} design={MULTI as never} claim={null} onChanged={jest.fn()} />);
  const picker = (await screen.findByLabelText(/which contrast/i)) as HTMLSelectElement;
  expect(Array.from(picker.options).map((o) => o.textContent)).toEqual(
    expect.arrayContaining([expect.stringContaining("Nkx2.2 KO"), expect.stringContaining("NKX2.2 differential")]),
  );
  await userEvent.selectOptions(picker, "0");
  const name = screen.getByLabelText(/contrast name/i) as HTMLInputElement;
  expect(name.value).toBe("Nkx2.2 KO vs control (RNA-seq)");
});

test("a single-contrast paper shows no picker", async () => {
  render(<Level3Gate studyId={1} design={DESIGN as never} claim={null} onChanged={jest.fn()} />);
  await screen.findByLabelText(/contrast name/i);
  expect(screen.queryByLabelText(/which contrast/i)).not.toBeInTheDocument();
});

test("tells the server which contrast was being edited, so a model pick is not credited to a human override", async () => {
  render(<Level3Gate studyId={1} design={MULTI as never} claim={null} onChanged={jest.fn()} />);
  await screen.findByLabelText(/contrast name/i);
  await userEvent.click(screen.getByRole("button", { name: /save design/i }));

  await waitFor(() => {
    const call = mockPut.mock.calls.find((c) => String(c[0]).includes("/differential-design"));
    expect(call?.[1].selected_contrast_index).toBe(1);
  });
});

test("sends the index the person moved to when they override the choice", async () => {
  render(<Level3Gate studyId={1} design={MULTI as never} claim={null} onChanged={jest.fn()} />);
  await userEvent.selectOptions(await screen.findByLabelText(/which contrast/i), "0");
  await userEvent.click(screen.getByRole("button", { name: /save design/i }));

  await waitFor(() => {
    const call = mockPut.mock.calls.find((c) => String(c[0]).includes("/differential-design"));
    expect(call?.[1].selected_contrast_index).toBe(0);
  });
});
