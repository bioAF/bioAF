"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { loadFailureMessage, logError } from "@/lib/errorReporting";
import {
  PdfDeadlineError,
  openPdfSession,
  renderWithDeadline,
  type PdfEngine,
  type PdfWorkerHandle,
  type PdfRenderTask,
  type PdfSession,
} from "@/lib/pdfSession";

/** The parts of a pdf.js document and page the viewers draw with. */
interface PdfViewport {
  width: number;
  height: number;
}
interface PdfPage<V extends PdfViewport> {
  getViewport(params: { scale: number }): V;
  render(params: {
    canvas: HTMLCanvasElement;
    canvasContext: CanvasRenderingContext2D;
    viewport: V;
  }): PdfRenderTask;
}
export interface PdfDocument<V extends PdfViewport = PdfViewport> {
  numPages: number;
  getPage(n: number): Promise<PdfPage<V>>;
}

export interface PdfDocumentState {
  /** 0 until a document is open. */
  numPages: number;
  page: number;
  setPage: (update: (page: number) => number) => void;
  /** True while bytes are present and the document is not yet open or failed. */
  opening: boolean;
  /** A document-level failure; the document is closed and its worker stopped. */
  error: string | null;
  /** The current page could not be drawn; other pages stay reachable. */
  pageFailed: boolean;
}

/**
 * Opens `data` with pdf.js and draws the current page on `canvasRef`, with
 * every piece of work bounded (see `pdfSession.ts`): the document and its
 * worker are stopped when `data` changes, the viewer unmounts, opening fails or
 * misses its deadline, or a page drawing misses its deadline (a worker that
 * has not finished one page in that time is not trusted with the next). A page
 * turn cancels a drawing still in progress, so a slow page cannot paint over
 * the current one. `what` names the document in logs.
 */
export function usePdfDocument<
  V extends PdfViewport,
  D extends PdfDocument<V>,
  W extends PdfWorkerHandle,
>(
  engine: PdfEngine<D, W>,
  data: ArrayBuffer | null,
  canvasRef: RefObject<HTMLCanvasElement | null>,
  scale: number,
  what: string,
): PdfDocumentState {
  const docRef = useRef<D | null>(null);
  const sessionRef = useRef<PdfSession<D> | null>(null);
  const engineRef = useRef(engine);
  engineRef.current = engine;

  const [numPages, setNumPages] = useState(0);
  const [opened, setOpened] = useState(false);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [pageFailed, setPageFailed] = useState(false);

  useEffect(() => {
    docRef.current = null;
    setOpened(false);
    setNumPages(0);
    setPage(1);
    setError(null);
    setPageFailed(false);
    if (!data) return;

    let cancelled = false;
    const session = openPdfSession(engineRef.current, data);
    sessionRef.current = session;
    session.document
      .then((doc) => {
        if (cancelled) return;
        docRef.current = doc;
        setOpened(true);
        setNumPages(doc.numPages);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        logError(`opening ${what}`, e);
        setError(
          e instanceof PdfDeadlineError || !(e instanceof Error)
            ? loadFailureMessage("The PDF")
            : e.message,
        );
      });
    return () => {
      cancelled = true;
      sessionRef.current = null;
      docRef.current = null;
      session.stop();
    };
    // `what` only labels log lines.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  useEffect(() => {
    const doc = docRef.current;
    if (!doc || numPages === 0) return;
    let cancelled = false;
    let renderTask: PdfRenderTask | null = null;
    setPageFailed(false);
    (async () => {
      const pdfPage = await doc.getPage(page);
      if (cancelled) return;
      const canvas = canvasRef.current;
      if (!canvas) return;
      const viewport = pdfPage.getViewport({ scale });
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      renderTask = pdfPage.render({ canvas, canvasContext: ctx, viewport });
      await renderWithDeadline(renderTask);
    })().catch((e: unknown) => {
      if (cancelled) return;
      logError(`rendering page ${page} of ${what}`, e);
      if (e instanceof PdfDeadlineError) {
        sessionRef.current?.stop();
        docRef.current = null;
        setError(loadFailureMessage("The PDF"));
      } else {
        setPageFailed(true);
      }
    });
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, numPages, scale]);

  return {
    numPages,
    page,
    setPage,
    opening: data !== null && !opened && error === null,
    error,
    pageFailed,
  };
}
