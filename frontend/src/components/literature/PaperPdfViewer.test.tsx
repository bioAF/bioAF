import { render, screen, waitFor } from "@testing-library/react";
import { PaperPdfViewer } from "./PaperPdfViewer";

jest.mock("@/lib/literature", () => ({
  fetchPaperPdfObjectUrl: jest.fn(),
}));

import { fetchPaperPdfObjectUrl } from "@/lib/literature";
const mockFetch = fetchPaperPdfObjectUrl as jest.Mock;

beforeEach(() => {
  mockFetch.mockReset();
  global.URL.revokeObjectURL = jest.fn();
});

test("renders the PDF in an iframe once the object URL resolves", async () => {
  mockFetch.mockResolvedValue("blob:abc");
  render(<PaperPdfViewer paperId={3} />);
  await waitFor(() => {
    const frame = screen.getByTitle("Paper PDF") as HTMLIFrameElement;
    expect(frame).toBeInTheDocument();
    expect(frame.getAttribute("src")).toContain("blob:abc");
  });
  expect(mockFetch).toHaveBeenCalledWith(3);
});

test("offers a download link pointing at the object URL", async () => {
  mockFetch.mockResolvedValue("blob:abc");
  render(<PaperPdfViewer paperId={3} />);
  await waitFor(() => {
    const link = screen.getByText(/download pdf/i) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("blob:abc");
  });
});

test("shows an error message when the PDF cannot be loaded", async () => {
  mockFetch.mockRejectedValue(new Error("No PDF attached to this paper."));
  render(<PaperPdfViewer paperId={3} />);
  await waitFor(() => {
    expect(screen.getByText(/no pdf attached/i)).toBeInTheDocument();
  });
});
