/**
 * The provenance preview shows metadata that users and pipelines named
 * (entity names, filenames, pipeline names). It is built from the report's
 * JSON as React text, so metadata can never become markup, a link, an image
 * or a heading.
 */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { ProvenanceReportPanel } from "./ProvenanceReportPanel";

const mockGet = jest.fn();
const mockDownload = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    download: (...args: unknown[]) => mockDownload(...args),
  },
}));

let mockCanAccess = true;
jest.mock("@/hooks/usePermissions", () => ({
  usePermissions: () => ({ canAccess: () => mockCanAccess, loading: false }),
}));

const HOSTILE = [
  "[Re-authenticate to view](https://evil.example)",
  "![Tracker](https://tracker.example/i.png)",
  "https://evil.example/bare",
  '<img src=x onerror="alert(1)">',
  "# Fake heading",
  "**bold**",
];

function report(overrides: Record<string, unknown> = {}) {
  return {
    report_type: "artifact",
    schema_version: "1.0",
    generated_at: "2026-03-11T10:00:00Z",
    entity: {
      id: 7,
      filename: HOSTILE[0],
      description: HOSTILE[3],
      owner: { name: HOSTILE[4], email: "a@b.c" },
      empty_value: "",
      missing_value: null,
      samples: [{}, {}],
      tags: [{}],
      files: { raw: [{}, {}, {}], results: [{}] },
      source: {
        type: "notebook_session",
        notebook_session: {
          id: 3,
          session_type: "jupyter",
          status: "stopped",
          cpu_cores: 4,
          memory_gb: 16,
          started_at: "2026-03-11T10:00:00Z",
          git_branch_name: HOSTILE[5],
          environment: {
            environment_name: HOSTILE[1],
            version_number: 2,
            build_number: 5,
            definition_format: "conda",
            image_uri: HOSTILE[2],
          },
          input_files: [
            { filename: '<img src=x onerror="alert(1)">.csv', file_type: "csv" },
            { filename: "[x](javascript:alert(1)).h5ad", file_type: "h5ad" },
          ],
        },
        pipeline_run: { id: 11, pipeline_name: HOSTILE[0], pipeline_version: "1.2", status: "completed" },
      },
    },
    audit_trail: [{}, {}, {}],
    ...overrides,
  };
}

async function openPreview(entityName = "Sample <b>X</b>") {
  render(<ProvenanceReportPanel entityType="artifact" entityId={7} entityName={entityName} />);
  fireEvent.click(screen.getByRole("button", { name: "Preview Report" }));
  return await screen.findByTestId("provenance-preview");
}

beforeEach(() => {
  mockGet.mockReset();
  mockDownload.mockReset();
  mockCanAccess = true;
});

describe("Provenance preview", () => {
  test("metadata stays literal text: no links, images, markup or extra headings", async () => {
    mockGet.mockResolvedValue(report());
    const preview = await openPreview();
    expect(preview.querySelector("a, img, script, b, em, code")).toBeNull();
    for (const value of HOSTILE) expect(preview).toHaveTextContent(value, { normalizeWhitespace: false });
    expect(preview).toHaveTextContent("Sample <b>X</b>");
    expect(preview).toHaveTextContent('<img src=x onerror="alert(1)">.csv (csv)');
    expect(preview).toHaveTextContent("[x](javascript:alert(1)).h5ad (h5ad)");
    const headings = Array.from(preview.querySelectorAll("h1, h2, h3, h4, h5, h6")).map((h) => h.textContent);
    expect(headings).toEqual([
      "Provenance Report: Sample <b>X</b>",
      "Entity Details",
      "Source",
      "Environment",
      "Input Files (2)",
      "Related Data",
      "Audit Trail",
    ]);
  });

  test("keeps the report's sections, labels and counts", async () => {
    mockGet.mockResolvedValue(report());
    const preview = await openPreview("Run 7");
    expect(mockGet).toHaveBeenCalledWith("/api/files/7/provenance/report?format=json");
    expect(preview).toHaveTextContent("Type: artifact");
    expect(preview).toHaveTextContent("Schema Version: 1.0");
    expect(preview).toHaveTextContent("Generated:");
    expect(preview).toHaveTextContent("id: 7");
    expect(preview).toHaveTextContent(`owner: ${HOSTILE[4]}`);
    expect(preview).not.toHaveTextContent("empty_value");
    expect(preview).not.toHaveTextContent("missing_value");
    expect(preview).toHaveTextContent("Source Type: notebook_session");
    expect(preview).toHaveTextContent("Resources: 4 CPU, 16 GB");
    expect(preview).toHaveTextContent("Version: v2.5");
    expect(preview).not.toHaveTextContent("Stopped:");
    expect(preview).toHaveTextContent("Run ID: 11");
    expect(preview).toHaveTextContent("Samples: 2 items");
    expect(preview).toHaveTextContent("Tags: 1 item");
    expect(preview).toHaveTextContent("Files: 3 raw, 1 results");
    expect(preview).toHaveTextContent("3 entries");
    const related = within(preview).getByRole("heading", { name: "Related Data" });
    expect(related.tagName).toBe("H2");
  });

  test("omits sections the report does not have", async () => {
    mockGet.mockResolvedValue({ report_type: "project", schema_version: "1.0", generated_at: "2026-03-11T10:00:00Z" });
    const preview = await openPreview("P");
    expect(within(preview).queryByRole("heading", { name: "Entity Details" })).toBeNull();
    expect(within(preview).queryByRole("heading", { name: "Audit Trail" })).toBeNull();
    expect(preview).toHaveTextContent("Type: project");
  });

  test("a failed preview says so and toggles closed", async () => {
    mockGet.mockRejectedValue(new Error("boom"));
    const preview = await openPreview();
    expect(preview).toHaveTextContent("Failed to load report preview.");
    fireEvent.click(screen.getByRole("button", { name: "Hide Preview" }));
    await waitFor(() => expect(screen.queryByTestId("provenance-preview")).toBeNull());
  });

  test("downloads still go to the report endpoint in the chosen format", async () => {
    mockDownload.mockResolvedValue(undefined);
    render(<ProvenanceReportPanel entityType="experiment" entityId={3} entityName="E" />);
    fireEvent.click(screen.getByRole("button", { name: /Download/ }));
    fireEvent.click(screen.getByRole("button", { name: "Markdown" }));
    await waitFor(() =>
      expect(mockDownload).toHaveBeenCalledWith("/api/experiments/3/provenance/report?format=md"),
    );
  });

  test("renders nothing without download permission", () => {
    mockCanAccess = false;
    const { container } = render(<ProvenanceReportPanel entityType="artifact" entityId={7} entityName="X" />);
    expect(container).toBeEmptyDOMElement();
  });
});
