import { render, screen, fireEvent } from "@testing-library/react";
import { PlotThumbnail } from "./PlotThumbnail";
import type { FileResponse, PlotArchiveResponse } from "@/lib/types";

jest.mock("@/hooks/useContentUrl", () => ({
  useFileContentUrl: (id: number | null) => (id ? `file-url-${id}` : null),
  usePlotThumbnailContentUrl: (id: number | null) => (id ? `thumb-url-${id}` : null),
}));

function fileStub(overrides: Partial<FileResponse> = {}): FileResponse {
  return {
    id: 7,
    filename: "plot.png",
    gcs_uri: "gs://bucket/plot.png",
    storage_uri: "gs://bucket/plot.png",
    size_bytes: 1024,
    md5_checksum: "abc",
    file_type: "png",
    tags: [],
    uploader: null,
    project_id: null,
    experiment_id: 1,
    sample_ids: [],
    source_type: "pipeline",
    source_pipeline_run_id: null,
    source_notebook_session_id: null,
    storage_deleted: false,
    upload_timestamp: "2026-05-14T00:00:00Z",
    created_at: "2026-05-14T00:00:00Z",
    ...overrides,
  };
}

function plot(overrides: Partial<PlotArchiveResponse> = {}): PlotArchiveResponse {
  return {
    id: 1,
    title: "UMAP",
    file: fileStub(),
    experiment_id: 1,
    experiment_name: null,
    project_name: null,
    pipeline_run_id: null,
    pipeline_run_name: null,
    notebook_session_id: null,
    notebook_session_type: null,
    source_type: null,
    tags: [],
    thumbnail_url: null,
    indexed_at: "2026-05-14T00:00:00Z",
    ...overrides,
  } as PlotArchiveResponse;
}

it("renders an image preview from file content for a non-PDF plot", () => {
  const onClick = jest.fn();
  render(<PlotThumbnail plot={plot()} onClick={onClick} />);
  const img = screen.getByAltText("UMAP") as HTMLImageElement;
  expect(img.src).toContain("file-url-7");
  fireEvent.click(img);
  expect(onClick).toHaveBeenCalledTimes(1);
});

it("renders the generated thumbnail for a PDF plot that has one", () => {
  render(
    <PlotThumbnail
      plot={plot({ file: fileStub({ file_type: "pdf" }), thumbnail_url: "x" })}
      onClick={jest.fn()}
    />,
  );
  const img = screen.getByAltText("UMAP") as HTMLImageElement;
  expect(img.src).toContain("thumb-url-1");
});

it("shows a PDF icon (still clickable) when a PDF has no thumbnail", () => {
  const onClick = jest.fn();
  render(
    <PlotThumbnail
      plot={plot({ file: fileStub({ file_type: "pdf" }), thumbnail_url: null })}
      onClick={onClick}
    />,
  );
  expect(screen.getByText("PDF")).toBeInTheDocument();
  expect(screen.getByText("No preview available")).toBeInTheDocument();
  fireEvent.click(screen.getByText("PDF"));
  expect(onClick).toHaveBeenCalledTimes(1);
});
