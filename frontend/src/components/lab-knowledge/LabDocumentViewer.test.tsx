import { render, screen, waitFor } from "@testing-library/react";
import { LabDocumentViewer } from "./LabDocumentViewer";

jest.mock("@/lib/labDocuments", () => ({
  fetchLabDocumentBlob: jest.fn(),
}));

const getDocumentMock = jest.fn();
jest.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: (...args: unknown[]) => getDocumentMock(...args),
}));

import { fetchLabDocumentBlob } from "@/lib/labDocuments";
const mockBlobFetch = fetchLabDocumentBlob as jest.Mock;

function makePdf(numPages: number) {
  return {
    numPages,
    getPage: jest.fn(async () => ({
      getViewport: () => ({ width: 600, height: 800 }),
      render: () => ({ promise: Promise.resolve() }),
    })),
  };
}

beforeEach(() => {
  mockBlobFetch.mockReset();
  getDocumentMock.mockReset();
  global.URL.createObjectURL = jest.fn(() => "blob:dl");
  global.URL.revokeObjectURL = jest.fn();
});

test("renders a paginated PDF preview", async () => {
  mockBlobFetch.mockResolvedValue({ arrayBuffer: async () => new ArrayBuffer(3) });
  getDocumentMock.mockReturnValue({ promise: Promise.resolve(makePdf(4)) });
  render(
    <LabDocumentViewer documentId={1} mimeType="application/pdf" fileName="manual.pdf" />,
  );
  await waitFor(() => expect(screen.getByText(/page 1 \/ 4/i)).toBeInTheDocument());
  expect(mockBlobFetch).toHaveBeenCalledWith(1, undefined);
});

test("renders an image preview", async () => {
  mockBlobFetch.mockResolvedValue({});
  render(<LabDocumentViewer documentId={2} mimeType="image/png" fileName="diagram.png" />);
  const img = (await screen.findByAltText("diagram.png")) as HTMLImageElement;
  expect(img.getAttribute("src")).toBe("blob:dl");
});

test("renders a text preview", async () => {
  mockBlobFetch.mockResolvedValue({ text: async () => "protocol step 1" });
  render(<LabDocumentViewer documentId={3} mimeType="text/plain" fileName="notes.txt" />);
  await waitFor(() => expect(screen.getByText(/protocol step 1/i)).toBeInTheDocument());
});

test("falls back to a download link for unpreviewable types", async () => {
  mockBlobFetch.mockResolvedValue({});
  render(
    <LabDocumentViewer documentId={4} mimeType="application/zip" fileName="bundle.zip" />,
  );
  await waitFor(() =>
    expect(screen.getByText(/inline preview isn't available/i)).toBeInTheDocument(),
  );
  expect(screen.getByText(/download/i)).toBeInTheDocument();
});
