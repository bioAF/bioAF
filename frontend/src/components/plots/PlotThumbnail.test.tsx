import { render, screen, fireEvent } from "@testing-library/react";
import { PlotThumbnail } from "./PlotThumbnail";
import type { PlotArchiveResponse } from "@/lib/types";

jest.mock("@/hooks/useContentUrl", () => ({
  useFileContentUrl: (id: number | null) => (id ? `file-url-${id}` : null),
  usePlotThumbnailContentUrl: (id: number | null) => (id ? `thumb-url-${id}` : null),
}));

function plot(overrides: Partial<PlotArchiveResponse> = {}): PlotArchiveResponse {
  return {
    id: 1,
    title: "UMAP",
    file: { id: 7, file_type: "png", storage_deleted: false },
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
      plot={plot({ file: { id: 7, file_type: "pdf", storage_deleted: false }, thumbnail_url: "x" })}
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
      plot={plot({ file: { id: 7, file_type: "pdf", storage_deleted: false }, thumbnail_url: null })}
      onClick={onClick}
    />,
  );
  expect(screen.getByText("PDF")).toBeInTheDocument();
  expect(screen.getByText("No preview available")).toBeInTheDocument();
  fireEvent.click(screen.getByText("PDF"));
  expect(onClick).toHaveBeenCalledTimes(1);
});
