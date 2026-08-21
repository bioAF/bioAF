import { render, screen, fireEvent } from "@testing-library/react";
import { SamplesheetColumnEditor } from "./SamplesheetColumnEditor";
import type { DeclaredColumn } from "@/lib/types";

/**
 * The editor for a pipeline that publishes no samplesheet contract at all.
 *
 * Seventeen of them are in the catalog, and until now bioAF emitted a fixed
 * `sample,fastq_1,fastq_2` header for every one and ignored everything the
 * scientist stated. This is where they say what the columns actually are.
 *
 * Two properties carry the weight:
 *
 * **A column says where its value comes from.** That is the whole point of a
 * binding, and a column bound to nothing is a question asked per sample rather
 * than a blank the launch silently accepts.
 *
 * **It opens on a working sheet, not an empty one.** A scientist who opens the
 * editor and changes nothing must get the file they got before, because the
 * editor must not be a way to break a launch that already works.
 */

const DEFAULT_COLUMNS: DeclaredColumn[] = [
  { name: "sample", type: "string", required: true, binding: { source: "sample_field", key: "external_id" } },
  { name: "fastq_1", type: "file", required: false, binding: { source: "read", key: "1" } },
  { name: "fastq_2", type: "file", required: false, binding: { source: "read", key: "2" } },
];

function renderEditor(columns: DeclaredColumn[] = DEFAULT_COLUMNS) {
  const onChange = jest.fn();
  const utils = render(
    <SamplesheetColumnEditor
      columns={columns}
      fileTypes={["fastq", "bam", "tiff"]}
      customFields={["panel"]}
      onChange={onChange}
    />,
  );
  return { onChange, ...utils };
}

describe("what the editor shows", () => {
  it("lists every declared column with its binding", () => {
    renderEditor();

    expect(screen.getByDisplayValue("sample")).toBeInTheDocument();
    expect(screen.getByDisplayValue("fastq_1")).toBeInTheDocument();
    expect(screen.getByDisplayValue("fastq_2")).toBeInTheDocument();
  });

  it("says plainly that an unbound column is asked per sample", () => {
    renderEditor([{ name: "cycle", type: "string", required: true }]);

    expect(screen.getByText(/asked for each sample in the grid/i)).toBeInTheDocument();
  });

  it("offers the sheet bioAF would emit anyway when nothing is declared", () => {
    renderEditor([]);

    expect(screen.getByRole("button", { name: /start from the standard sheet/i })).toBeInTheDocument();
  });
});

describe("editing a column", () => {
  it("renames a column", () => {
    const { onChange } = renderEditor();

    fireEvent.change(screen.getByDisplayValue("fastq_1"), { target: { value: "R1" } });

    expect(onChange).toHaveBeenCalledWith([
      DEFAULT_COLUMNS[0],
      { ...DEFAULT_COLUMNS[1], name: "R1" },
      DEFAULT_COLUMNS[2],
    ]);
  });

  it("changes where a column's value comes from", () => {
    const { onChange } = renderEditor([{ name: "image", type: "file", required: true }]);

    fireEvent.change(screen.getByLabelText(/value for image comes from/i), {
      target: { value: "file_type" },
    });

    expect(onChange).toHaveBeenCalledWith([
      { name: "image", type: "file", required: true, binding: { source: "file_type", key: "" } },
    ]);
  });

  it("keeps the key when the source is unchanged and clears it when it changes", () => {
    const { onChange } = renderEditor([
      { name: "image", type: "file", required: true, binding: { source: "file_type", key: "tiff" } },
    ]);

    fireEvent.change(screen.getByLabelText(/value for image comes from/i), {
      target: { value: "literal" },
    });

    expect(onChange).toHaveBeenCalledWith([
      { name: "image", type: "file", required: true, binding: { source: "literal", key: "" } },
    ]);
  });

  it("offers the sample's own file types rather than free text", () => {
    renderEditor([
      { name: "image", type: "file", required: true, binding: { source: "file_type", key: "tiff" } },
    ]);

    const picker = screen.getByLabelText(/which file for image/i);
    expect(picker.tagName).toBe("SELECT");
    expect(screen.getByRole("option", { name: "bam" })).toBeInTheDocument();
  });

  it("adds a column", () => {
    const { onChange } = renderEditor([]);

    fireEvent.click(screen.getByRole("button", { name: /add a column/i }));

    expect(onChange).toHaveBeenCalledWith([{ name: "", type: "string", required: false }]);
  });

  it("removes a column", () => {
    const { onChange } = renderEditor();

    fireEvent.click(screen.getByRole("button", { name: /remove fastq_2/i }));

    expect(onChange).toHaveBeenCalledWith([DEFAULT_COLUMNS[0], DEFAULT_COLUMNS[1]]);
  });

  it("moves a column, because the order it declares is the order emitted", () => {
    const { onChange } = renderEditor();

    fireEvent.click(screen.getByRole("button", { name: /move fastq_1 up/i }));

    expect(onChange).toHaveBeenCalledWith([DEFAULT_COLUMNS[1], DEFAULT_COLUMNS[0], DEFAULT_COLUMNS[2]]);
  });

  it("starts from the standard sheet on request", () => {
    const { onChange } = renderEditor([]);

    fireEvent.click(screen.getByRole("button", { name: /start from the standard sheet/i }));

    expect(onChange).toHaveBeenCalledWith(DEFAULT_COLUMNS);
  });
});

describe("what it refuses to let through", () => {
  it("warns about a duplicate column name", () => {
    renderEditor([
      { name: "sample", type: "string", required: true },
      { name: "sample", type: "string", required: false },
    ]);

    // Both offenders are flagged: naming only the second would send the
    // scientist to rename the wrong one.
    expect(screen.getAllByText(/declared twice/i)).toHaveLength(2);
  });

  it("warns about a column with no name", () => {
    renderEditor([{ name: "", type: "string", required: false }]);

    expect(screen.getByText(/needs a name/i)).toBeInTheDocument();
  });

  it("warns when a binding names nothing", () => {
    renderEditor([{ name: "image", type: "file", required: true, binding: { source: "file_type", key: "" } }]);

    expect(screen.getByText(/say which file/i)).toBeInTheDocument();
  });
});
