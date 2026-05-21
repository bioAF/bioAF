import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PaperPdfViewer } from "./PaperPdfViewer";

jest.mock("@/lib/literature", () => ({
  fetchPaperPdfBlob: jest.fn(),
}));

const getDocumentMock = jest.fn();
jest.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: (...args: unknown[]) => getDocumentMock(...args),
}));

import { fetchPaperPdfBlob } from "@/lib/literature";
const mockBlobFetch = fetchPaperPdfBlob as jest.Mock;

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
  mockBlobFetch.mockResolvedValue({ arrayBuffer: async () => new ArrayBuffer(3) });
});

test("renders the page counter and a download link once the document loads", async () => {
  getDocumentMock.mockReturnValue({ promise: Promise.resolve(makePdf(5)) });
  render(<PaperPdfViewer paperId={3} />);
  await waitFor(() =>
    expect(screen.getByText(/page 1 \/ 5/i)).toBeInTheDocument(),
  );
  const link = screen.getByText(/download pdf/i) as HTMLAnchorElement;
  expect(link.getAttribute("href")).toBe("blob:dl");
  expect(mockBlobFetch).toHaveBeenCalledWith(3);
});

test("Next advances the page and reports the reached page to the parent", async () => {
  getDocumentMock.mockReturnValue({ promise: Promise.resolve(makePdf(2)) });
  const onReach = jest.fn();
  render(<PaperPdfViewer paperId={3} onReachPage={onReach} />);
  await waitFor(() =>
    expect(screen.getByText(/page 1 \/ 2/i)).toBeInTheDocument(),
  );
  expect(onReach).toHaveBeenCalledWith(1, 2);

  await userEvent.click(screen.getByRole("button", { name: /next/i }));
  await waitFor(() =>
    expect(screen.getByText(/page 2 \/ 2/i)).toBeInTheDocument(),
  );
  expect(onReach).toHaveBeenCalledWith(2, 2);
});

test("disables Prev on the first page and Next on the last page", async () => {
  getDocumentMock.mockReturnValue({ promise: Promise.resolve(makePdf(1)) });
  render(<PaperPdfViewer paperId={3} />);
  await waitFor(() =>
    expect(screen.getByText(/page 1 \/ 1/i)).toBeInTheDocument(),
  );
  expect(screen.getByRole("button", { name: /prev/i })).toBeDisabled();
  expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
});

test("shows an error message when the PDF cannot be loaded", async () => {
  mockBlobFetch.mockRejectedValue(new Error("No PDF attached to this paper."));
  render(<PaperPdfViewer paperId={3} />);
  await waitFor(() =>
    expect(screen.getByText(/no pdf attached/i)).toBeInTheDocument(),
  );
});
