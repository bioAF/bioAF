import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PaperPdfViewer } from "./PaperPdfViewer";
import { PDF_DEADLINES } from "@/lib/pdfSession";

jest.mock("@/lib/literature", () => ({
  fetchPaperPdfBlob: jest.fn(),
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

import { fetchPaperPdfBlob } from "@/lib/literature";
const mockBlobFetch = fetchPaperPdfBlob as jest.Mock;

function makePdf(numPages: number) {
  return {
    numPages,
    getPage: jest.fn(async () => ({
      getViewport: () => ({ width: 600, height: 800 }),
      render: () => ({ promise: Promise.resolve(), cancel: jest.fn() }),
    })),
  };
}

// jsdom has no Web Worker; the viewer creates one per document for pdf.js.
const mockWebWorkers: Array<{ terminate: jest.Mock }> = [];
global.Worker = class {
  terminate = jest.fn();
  constructor() {
    mockWebWorkers.push(this);
  }
} as unknown as typeof Worker;

beforeEach(() => {
  mockBlobFetch.mockReset();
  getDocumentMock.mockReset();
  global.URL.createObjectURL = jest.fn(() => "blob:dl");
  global.URL.revokeObjectURL = jest.fn();
  mockBlobFetch.mockResolvedValue({ arrayBuffer: async () => new ArrayBuffer(3) });
});

// A paper PDF is attacker-supplied content: it arrives from an external
// publisher or an upload, not from us. pdf.js runs embedded PDF JavaScript by
// default, which is arbitrary script in our origin (GHSA-hq66-cqwq-w95j).
// Nothing in the viewer needs it, so it stays off.
test("loads the document with PDF JavaScript and eval disabled", async () => {
  getDocumentMock.mockReturnValue({ promise: Promise.resolve(makePdf(1)) });
  render(<PaperPdfViewer paperId={3} />);
  await waitFor(() => expect(getDocumentMock).toHaveBeenCalled());
  expect(getDocumentMock).toHaveBeenCalledWith(
    expect.objectContaining({ enableScripting: false, isEvalSupported: false }),
  );
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

// pdf.js parses on a worker that stays alive until its loading task is
// destroyed. A viewer that is closed, or moved to another paper, must stop it;
// otherwise a hostile or huge PDF keeps a worker busy after the reader left.
describe("pdf.js lifecycle", () => {
  function loadingTask(promise: Promise<unknown>) {
    return { promise, destroy: jest.fn() };
  }

  test("closing the viewer while the PDF is still loading stops pdf.js", async () => {
    const task = loadingTask(new Promise(() => {}));
    getDocumentMock.mockReturnValue(task);
    const { unmount } = render(<PaperPdfViewer paperId={3} />);
    await waitFor(() => expect(getDocumentMock).toHaveBeenCalled());
    unmount();
    expect(task.destroy).toHaveBeenCalled();
  });

  test("closing the viewer after the PDF loaded stops pdf.js", async () => {
    const task = loadingTask(Promise.resolve(makePdf(2)));
    getDocumentMock.mockReturnValue(task);
    const { unmount } = render(<PaperPdfViewer paperId={3} />);
    await screen.findByText(/page 1 \/ 2/i);
    unmount();
    expect(task.destroy).toHaveBeenCalled();
  });

  test("opening another paper stops the previous one, and its late result is never shown", async () => {
    let finishFirst: (pdf: unknown) => void = () => {};
    const first = loadingTask(new Promise((resolve) => (finishFirst = resolve)));
    const second = loadingTask(Promise.resolve(makePdf(7)));
    getDocumentMock.mockReturnValueOnce(first).mockReturnValueOnce(second);

    const { rerender } = render(<PaperPdfViewer paperId={3} />);
    await waitFor(() => expect(getDocumentMock).toHaveBeenCalledTimes(1));
    rerender(<PaperPdfViewer paperId={4} />);
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
    render(<PaperPdfViewer paperId={3} />);
    await screen.findByText(/invalid pdf structure/i);
    expect(task.destroy).toHaveBeenCalled();
  });
});

describe("page drawing", () => {
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
    render(<PaperPdfViewer paperId={3} />);

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
    render(<PaperPdfViewer paperId={3} />);
    await screen.findByText(/page 1 \/ 2/i);
    await waitFor(() => expect(pdf.getPage).toHaveBeenCalledWith(1));

    await userEvent.click(screen.getByRole("button", { name: /next/i }));
    await screen.findByText(/page 2 \/ 2/i);
    expect(stale.cancel).toHaveBeenCalled();
    expect(screen.queryByText(/could not be loaded/i)).not.toBeInTheDocument();
  });
});

// A hostile file can keep pdf.js's worker busy forever, and pdf.js's own
// destroy() waits for that worker to answer. The viewer must give up within a
// fixed time, say so, and terminate the worker itself.
describe("stuck PDFs", () => {
  beforeEach(() => {
    mockWorkers.length = 0;
    mockWebWorkers.length = 0;
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
    render(<PaperPdfViewer paperId={3} />);
    await act(() => jest.advanceTimersByTimeAsync(0)); // fetch the bytes, start opening
    await act(() => jest.advanceTimersByTimeAsync(PDF_DEADLINES.loadMs - 1));
    expect(screen.getByText(/loading pdf/i)).toBeInTheDocument();

    await act(() => jest.advanceTimersByTimeAsync(1));
    expect(screen.getByText(/the pdf could not be loaded/i)).toBeInTheDocument();
    await act(() => jest.advanceTimersByTimeAsync(PDF_DEADLINES.shutdownGraceMs));
    expect(mockWebWorkers).toHaveLength(1);
    expect(mockWebWorkers[0].terminate).toHaveBeenCalled();
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
    render(<PaperPdfViewer paperId={3} />);
    await act(() => jest.advanceTimersByTimeAsync(0));
    expect(screen.getByText(/page 1 \/ 2/i)).toBeInTheDocument();

    await act(() => jest.advanceTimersByTimeAsync(PDF_DEADLINES.renderMs));
    expect(screen.getByText(/the pdf could not be loaded/i)).toBeInTheDocument();
    await act(() => jest.advanceTimersByTimeAsync(PDF_DEADLINES.shutdownGraceMs));
    expect(mockWebWorkers[0].terminate).toHaveBeenCalled();
  });

  test("closing a viewer whose worker never answers still terminates the worker", async () => {
    getDocumentMock.mockReturnValue({ promise: Promise.resolve(makePdf(2)), destroy: jest.fn(stuck) });
    const { unmount } = render(<PaperPdfViewer paperId={3} />);
    await act(() => jest.advanceTimersByTimeAsync(0));
    expect(screen.getByText(/page 1 \/ 2/i)).toBeInTheDocument();
    unmount();
    expect(mockWebWorkers[0].terminate).not.toHaveBeenCalled();
    await act(() => jest.advanceTimersByTimeAsync(PDF_DEADLINES.shutdownGraceMs));
    expect(mockWebWorkers[0].terminate).toHaveBeenCalled();
  });
});
