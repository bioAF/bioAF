/**
 * Bounded open, draw and stop for a pdf.js document.
 *
 * pdf.js stops a document by asking its worker to terminate and waiting for
 * the reply (`PDFDocumentLoadingTask.destroy()`). A worker stuck inside a
 * hostile or broken file never replies, so that wait never ends and the worker
 * keeps running after the reader has moved on. pdf.js's own PDFWorker cannot
 * help either: it only exposes its Web Worker for termination once the worker
 * has reported ready. A session therefore owns its worker (the engine creates
 * the Web Worker and hands it to pdf.js as a port) and terminates it itself
 * when the polite stop does not finish in time. Opening a
 * document and drawing a page each get a deadline too, because nothing in
 * pdf.js bounds them.
 *
 * Deliberately free of any `pdfjs-dist` import (see `pdfSecurity.ts`): the
 * viewers pass the pdf.js module to `createPdfEngine`.
 */
import { logError } from "@/lib/errorReporting";
import { HARDENED_PDF_OPTIONS } from "@/lib/pdfSecurity";

export const PDF_DEADLINES = {
  /** Parsing the document structure; the bytes are already downloaded. */
  loadMs: 30_000,
  /** Parsing and drawing one page. */
  renderMs: 30_000,
  /** How long the worker gets to acknowledge a stop before it is terminated. */
  shutdownGraceMs: 2_000,
};

export class PdfDeadlineError extends Error {
  constructor(what: string, ms: number) {
    super(`${what} did not finish within ${ms} ms`);
    this.name = "PdfDeadlineError";
  }
}

/** The parts of pdf.js a session uses. */
export interface PdfWorkerHandle {
  /** Terminates the Web Worker immediately, whatever it is doing. */
  destroy(): void;
}
export interface PdfLoadingTask<D> {
  promise: Promise<D>;
  destroy(): Promise<void>;
}
export interface PdfEngine<D, W extends PdfWorkerHandle = PdfWorkerHandle> {
  createWorker(): W;
  open(data: ArrayBuffer, worker: W): PdfLoadingTask<D>;
}
export interface PdfRenderTask {
  promise: Promise<void>;
  cancel(): void;
}

export interface PdfSession<D> {
  /** Resolves with the opened document, or rejects (including on the load deadline). */
  document: Promise<D>;
  /** Stops the document and, within the grace period, its worker. Safe to call more than once. */
  stop(): void;
}

/** Rejects with a PdfDeadlineError if `promise` has not settled within `ms`. */
export function withDeadline<T>(promise: Promise<T>, ms: number, what: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new PdfDeadlineError(what, ms)), ms);
  });
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timer));
}

export function openPdfSession<D, W extends PdfWorkerHandle>(
  engine: PdfEngine<D, W>,
  data: ArrayBuffer,
  deadlines = PDF_DEADLINES,
): PdfSession<D> {
  const worker = engine.createWorker();
  const task = engine.open(data, worker);
  let stopped = false;

  const stop = () => {
    if (stopped) return;
    stopped = true;
    let terminated = false;
    const terminate = () => {
      if (terminated) return;
      terminated = true;
      clearTimeout(grace);
      try {
        worker.destroy();
      } catch (e) {
        logError("terminating the PDF worker", e);
      }
    };
    const grace = setTimeout(terminate, deadlines.shutdownGraceMs);
    let destroying: Promise<void>;
    try {
      destroying = Promise.resolve(task.destroy());
    } catch (e) {
      destroying = Promise.reject(e);
    }
    destroying.catch((e) => logError("stopping the PDF document", e)).finally(terminate);
  };

  const document = withDeadline(task.promise, deadlines.loadMs, "opening the PDF").catch(
    (e: unknown) => {
      stop();
      throw e;
    },
  );
  return { document, stop };
}

/** Waits for a page drawing, cancelling it if it misses the render deadline. */
export function renderWithDeadline(task: PdfRenderTask, deadlines = PDF_DEADLINES): Promise<void> {
  return withDeadline(task.promise, deadlines.renderMs, "drawing a PDF page").catch((e: unknown) => {
    if (e instanceof PdfDeadlineError) task.cancel();
    throw e;
  });
}

/** The parts of the pdf.js module an engine uses. */
interface PdfJsModule<D, PW extends { destroy(): void }> {
  GlobalWorkerOptions: { workerSrc: string };
  // pdf.js's declarations type `port` as null, but the runtime takes a Web
  // Worker (the same path as GlobalWorkerOptions.workerPort); `never` lets
  // the real module type-check against this shape.
  PDFWorker: new (params: { port: never }) => PW;
  getDocument(params: { data: ArrayBuffer; worker: PW } & typeof HARDENED_PDF_OPTIONS): PdfLoadingTask<D>;
}

/** A Web Worker the session created, and the pdf.js handle that talks to it. */
export interface OwnedPdfWorker<PW = unknown> extends PdfWorkerHandle {
  pdfWorker: PW;
}

/**
 * Opens documents with the hardened options, each on a Web Worker this app
 * creates (from pdf.js's configured `workerSrc`) and can always terminate.
 */
export function createPdfEngine<D, PW extends { destroy(): void }>(
  pdfjs: PdfJsModule<D, PW>,
): PdfEngine<D, OwnedPdfWorker<PW>> {
  return {
    createWorker() {
      const webWorker = new Worker(pdfjs.GlobalWorkerOptions.workerSrc, { type: "module" });
      const pdfWorker = new pdfjs.PDFWorker({ port: webWorker as never });
      return {
        pdfWorker,
        destroy() {
          try {
            pdfWorker.destroy();
          } finally {
            webWorker.terminate();
          }
        },
      };
    },
    open: (data, worker) =>
      pdfjs.getDocument({ data, worker: worker.pdfWorker, ...HARDENED_PDF_OPTIONS }),
  };
}
