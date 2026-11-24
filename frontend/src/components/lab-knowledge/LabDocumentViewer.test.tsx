import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LabDocumentViewer } from "./LabDocumentViewer";
import { PDF_DEADLINES } from "@/lib/pdfSession";

jest.mock("@/lib/labDocuments", () => ({
  fetchLabDocumentBlob: jest.fn(),
}));

const getDocumentMock = jest.fn();
// Every worker the viewer creates, so a test can see whether it was terminated.
const mockWorkers: Array<{ destroy: jest.Mock }> = [];
jest.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  PDFWorker: class {
    destroy = jest.fn();
    constructor() {
      mockWorkers.push(this);
    }
  },
  // A real loading task always has destroy(); tests that check it supply their own.
  getDocument: (...args: unknown[]) => ({ destroy: jest.fn(), ...getDocumentMock(...args) }),
}));

import { fetchLabDocumentBlob } from "@/lib/labDocuments";
const mockBlobFetch = fetchLabDocumentBlob as jest.Mock;

function makePdf(numPages: number) {
  return {
    numPages,
    getPage: jest.fn(async () => ({
      getViewport: () => ({ width: 600, height: 800 }),
      render: () => ({ promise: Promise.resolve(), cancel: jest.fn() }),
    })),
  };
}

beforeEach(() => {
  mockBlobFetch.mockReset();
  getDocumentMock.mockReset();
  global.URL.createObjectURL = jest.fn(() => "blob:dl");
  global.URL.revokeObjectURL = jest.fn();
});

// Lab documents are uploaded by users, so the bytes are untrusted. pdf.js runs
// embedded PDF JavaScript by default, which is arbitrary script in our origin
// (GHSA-hq66-cqwq-w95j). The viewer only paints pages, so it stays off.
test("loads the document with PDF JavaScript and eval disabled", async () => {
  mockBlobFetch.mockResolvedValue({ arrayBuffer: async () => new ArrayBuffer(3) });
  getDocumentMock.mockReturnValue({ promise: Promise.resolve(makePdf(1)) });
  render(
    <LabDocumentViewer documentId={1} mimeType="application/pdf" fileName="manual.pdf" />,
  );
  await waitFor(() => expect(getDocumentMock).toHaveBeenCalled());
  expect(getDocumentMock).toHaveBeenCalledWith(
    expect.objectContaining({ enableScripting: false, isEvalSupported: false }),
  );
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

// pdf.js parses on a worker that stays alive until its loading task is
// destroyed. Lab documents are user uploads, so a closed or replaced viewer
// must stop that worker rather than leave a hostile file being parsed.
describe("pdf.js lifecycle", () => {
  const PDF = { mimeType: "application/pdf", fileName: "manual.pdf" };

  function loadingTask(promise: Promise<unknown>) {
    return { promise, destroy: jest.fn() };
  }

  beforeEach(() => {
    mockBlobFetch.mockResolvedValue({ arrayBuffer: async () => new ArrayBuffer(3) });
  });

  test("closing the viewer while the PDF is still loading stops pdf.js", async () => {
    const task = loadingTask(new Promise(() => {}));
    getDocumentMock.mockReturnValue(task);
    const { unmount } = render(<LabDocumentViewer documentId={1} {...PDF} />);
    await waitFor(() => expect(getDocumentMock).toHaveBeenCalled());
    unmount();
    expect(task.destroy).toHaveBeenCalled();
  });

  test("closing the viewer after the PDF loaded stops pdf.js", async () => {
    const task = loadingTask(Promise.resolve(makePdf(2)));
    getDocumentMock.mockReturnValue(task);
    const { unmount } = render(<LabDocumentViewer documentId={1} {...PDF} />);
    await screen.findByText(/page 1 \/ 2/i);
    unmount();
    expect(task.destroy).toHaveBeenCalled();
  });

  test("opening another version stops the previous one, and its late result is never shown", async () => {
    let finishFirst: (pdf: unknown) => void = () => {};
    const first = loadingTask(new Promise((resolve) => (finishFirst = resolve)));
    const second = loadingTask(Promise.resolve(makePdf(7)));
    getDocumentMock.mockReturnValueOnce(first).mockReturnValueOnce(second);

    const { rerender } = render(<LabDocumentViewer documentId={1} version={1} {...PDF} />);
    await waitFor(() => expect(getDocumentMock).toHaveBeenCalledTimes(1));
    rerender(<LabDocumentViewer documentId={1} version={2} {...PDF} />);
    await screen.findByText(/page 1 \/ 7/i);
    expect(first.destroy).toHaveBeenCalled();

    finishFirst(makePdf(2));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByText(/page 1 \/ 7/i)).toBeInTheDocument();
    expect(second.destroy).not.toHaveBeenCalled();
  });

  test("a file pdf.js cannot read shows the error and stops pdf.js", async () => {
    const task = loadingTask(Promise.reject(new Error("Invalid PDF structure.")));
    getDocumentMock.mockReturnValue(task);
    render(<LabDocumentViewer documentId={1} {...PDF} />);
    await screen.findByText(/invalid pdf structure/i);
    expect(task.destroy).toHaveBeenCalled();
  });
});

describe("page drawing", () => {
  const PDF = { mimeType: "application/pdf", fileName: "manual.pdf" };

  function pdfWithPages(renders: Array<() => { promise: Promise<void>; cancel: jest.Mock }>) {
    let call = 0;
    return {
      numPages: renders.length,
      getPage: jest.fn(async () => ({
        getViewport: () => ({ width: 600, height: 800 }),
        render: () => renders[Math.min(call++, renders.length - 1)](),
      })),
    };
  }

  // jsdom has no 2D canvas; the drawing itself belongs to the mocked pdf.js.
  beforeEach(() => {
    mockBlobFetch.mockResolvedValue({ arrayBuffer: async () => new ArrayBuffer(3) });
    jest.spyOn(console, "error").mockImplementation(() => {});
    jest
      .spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue({} as unknown as CanvasRenderingContext2D);
  });
  afterEach(() => jest.restoreAllMocks());

  test("a page that fails to draw says so and leaves the other pages reachable", async () => {
    const pdf = pdfWithPages([
      () => ({ promise: Promise.reject(new Error("bad page stream")), cancel: jest.fn() }),
      () => ({ promise: Promise.resolve(), cancel: jest.fn() }),
    ]);
    getDocumentMock.mockReturnValue({ promise: Promise.resolve(pdf) });
    render(<LabDocumentViewer documentId={1} {...PDF} />);

    expect(await screen.findByText(/page 1 could not be loaded/i)).toBeInTheDocument();
    expect(screen.queryByText(/bad page stream/i)).not.toBeInTheDocument();
    const next = screen.getByRole("button", { name: /next/i });
    expect(next).toBeEnabled();

    await userEvent.click(next);
    await screen.findByText(/page 2 \/ 2/i);
    await waitFor(() =>
      expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument(),
    );
  });

  test("turning the page while one is still drawing cancels the stale drawing", async () => {
    const stale = { promise: new Promise<void>(() => {}), cancel: jest.fn() };
    const pdf = pdfWithPages([
      () => stale,
      () => ({ promise: Promise.resolve(), cancel: jest.fn() }),
    ]);
    getDocumentMock.mockReturnValue({ promise: Promise.resolve(pdf) });
    render(<LabDocumentViewer documentId={1} {...PDF} />);
    await screen.findByText(/page 1 \/ 2/i);
    await waitFor(() => expect(pdf.getPage).toHaveBeenCalledWith(1));

    await userEvent.click(screen.getByRole("button", { name: /next/i }));
    await screen.findByText(/page 2 \/ 2/i);
    expect(stale.cancel).toHaveBeenCalled();
    expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument();
  });
});

// A hostile upload can keep pdf.js's worker busy forever, and pdf.js's own
// destroy() waits for that worker to answer. The viewer must give up within a
// fixed time, say so, and terminate the worker itself.
describe("stuck PDFs", () => {
  const PDF = { mimeType: "application/pdf", fileName: "manual.pdf" };

  beforeEach(() => {
    mockWorkers.length = 0;
    mockBlobFetch.mockResolvedValue({ arrayBuffer: async () => new ArrayBuffer(3) });
    jest.useFakeTimers();
    jest.spyOn(console, "error").mockImplementation(() => {});
    jest
      .spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue({} as unknown as CanvasRenderingContext2D);
  });
  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  const stuck = () => new Promise<never>(() => {});

  test("a PDF that never finishes opening is abandoned and its worker terminated", async () => {
    getDocumentMock.mockReturnValue({ promise: stuck(), destroy: jest.fn(stuck) });
    render(<LabDocumentViewer documentId={1} {...PDF} />);
    await act(() => jest.advanceTimersByTimeAsync(0)); // fetch the bytes, start opening
    await act(() => jest.advanceTimersByTimeAsync(PDF_DEADLINES.loadMs - 1));
    expect(screen.getByText(/loading document/i)).toBeInTheDocument();

    await act(() => jest.advanceTimersByTimeAsync(1));
    expect(screen.getByText(/the pdf could not be loaded/i)).toBeInTheDocument();
    await act(() => jest.advanceTimersByTimeAsync(PDF_DEADLINES.shutdownGraceMs));
    expect(mockWorkers).toHaveLength(1);
    expect(mockWorkers[0].destroy).toHaveBeenCalled();
  });

  test("a page that never finishes drawing is abandoned and the worker terminated", async () => {
    const pdf = {
      numPages: 2,
      getPage: jest.fn(async () => ({
        getViewport: () => ({ width: 600, height: 800 }),
        render: () => ({ promise: stuck(), cancel: jest.fn() }),
      })),
    };
    getDocumentMock.mockReturnValue({ promise: Promise.resolve(pdf), destroy: jest.fn(stuck) });
    render(<LabDocumentViewer documentId={1} {...PDF} />);
    await act(() => jest.advanceTimersByTimeAsync(0));
    expect(screen.getByText(/page 1 \/ 2/i)).toBeInTheDocument();

    await act(() => jest.advanceTimersByTimeAsync(PDF_DEADLINES.renderMs));
    expect(screen.getByText(/the pdf could not be loaded/i)).toBeInTheDocument();
    await act(() => jest.advanceTimersByTimeAsync(PDF_DEADLINES.shutdownGraceMs));
    expect(mockWorkers[0].destroy).toHaveBeenCalled();
  });

  test("closing a viewer whose worker never answers still terminates the worker", async () => {
    getDocumentMock.mockReturnValue({ promise: Promise.resolve(makePdf(2)), destroy: jest.fn(stuck) });
    const { unmount } = render(<LabDocumentViewer documentId={1} {...PDF} />);
    await act(() => jest.advanceTimersByTimeAsync(0));
    expect(screen.getByText(/page 1 \/ 2/i)).toBeInTheDocument();
    unmount();
    expect(mockWorkers[0].destroy).not.toHaveBeenCalled();
    await act(() => jest.advanceTimersByTimeAsync(PDF_DEADLINES.shutdownGraceMs));
    expect(mockWorkers[0].destroy).toHaveBeenCalled();
  });
});
