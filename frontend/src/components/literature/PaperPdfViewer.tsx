"use client";

import { useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import type { PDFDocumentProxy, PageViewport } from "pdfjs-dist";
import { fetchPaperPdfBlob } from "@/lib/literature";
import { createPdfEngine, type OwnedPdfWorker } from "@/lib/pdfSession";
import { pageFailureMessage, usePdfDocument } from "@/hooks/usePdfDocument";

// pdf.js renders on a web worker. The bundler (Next/webpack) resolves this URL
// to an emitted asset served from our own origin, so there is no CDN dependency
// and nothing for a strict Content-Security-Policy to block.
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

// Each document gets its own worker so a stuck one can be terminated
// (src/lib/pdfSession.ts).
const ENGINE = createPdfEngine<PDFDocumentProxy, pdfjsLib.PDFWorker>(pdfjsLib);

interface Props {
  paperId: number;
  filename?: string;
  // Fired whenever the visible page changes (including the first render), with
  // the 1-based page number and the total page count. The parent uses it to
  // advance reading status.
  onReachPage?: (page: number, totalPages: number) => void;
}

const RENDER_SCALE = 1.4;

// Renders the paper's PDF one page at a time on a canvas, with Prev/Next
// pagination and a Download link. Replaces the old native-iframe viewer so the
// app can observe page turns and drive reading status from them.
export function PaperPdfViewer({ paperId, filename, onReachPage }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const onReachRef = useRef(onReachPage);
  onReachRef.current = onReachPage;

  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [data, setData] = useState<ArrayBuffer | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // Fetch the bytes (and a download URL) once per paper.
  useEffect(() => {
    let cancelled = false;
    let createdUrl: string | null = null;
    setData(null);
    setFetchError(null);

    fetchPaperPdfBlob(paperId)
      .then(async (blob) => {
        if (cancelled) return;
        createdUrl = URL.createObjectURL(blob);
        setDownloadUrl(createdUrl);
        const bytes = await blob.arrayBuffer();
        if (!cancelled) setData(bytes);
      })
      .catch((e) => {
        if (!cancelled) setFetchError(e instanceof Error ? e.message : "Could not load the PDF.");
      });

    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [paperId]);

  const pdf = usePdfDocument<PageViewport, PDFDocumentProxy, OwnedPdfWorker<pdfjsLib.PDFWorker>>(ENGINE, data, canvasRef, RENDER_SCALE, "a paper PDF");
  const { numPages, page, setPage, pageFailed } = pdf;
  const error = fetchError ?? pdf.error;
  const loading = !error && (data === null || pdf.opening);

  // Report the reached page to the parent on every page change.
  useEffect(() => {
    if (numPages > 0) onReachRef.current?.(page, numPages);
  }, [page, numPages]);

  if (loading) {
    return <div className="text-sm text-gray-500 p-4">Loading PDF...</div>;
  }
  if (error) {
    return <div className="text-sm text-red-700 p-4">{error}</div>;
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="border border-gray-300 px-3 py-1 rounded text-sm hover:bg-gray-50 disabled:opacity-40 disabled:hover:bg-transparent"
          >
            Prev
          </button>
          <span className="text-sm text-gray-600 tabular-nums">
            Page {page} / {numPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(numPages, p + 1))}
            disabled={page >= numPages}
            className="border border-gray-300 px-3 py-1 rounded text-sm hover:bg-gray-50 disabled:opacity-40 disabled:hover:bg-transparent"
          >
            Next
          </button>
        </div>
        {downloadUrl && (
          <a
            href={downloadUrl}
            download={filename ?? "paper.pdf"}
            className="text-bioaf-700 hover:underline text-sm"
          >
            Download PDF
          </a>
        )}
      </div>
      <div
        className="overflow-auto border border-gray-200 rounded bg-gray-50 flex items-center justify-center"
        style={{ height: "85vh" }}
      >
        {/* Fit the whole page inside the frame: max-w-full + max-h-full with the
            page's intrinsic ratio scale it down to fit both bounds, so a portrait
            page shows in full on a laptop or external display without an inner
            scrollbar. Sized for desktop; small screens are not a target. */}
        {pageFailed && (
          <p role="alert" className="text-sm text-red-700 p-4">
            {pageFailureMessage(page)}
          </p>
        )}
        <canvas ref={canvasRef} className={pageFailed ? "hidden" : "max-w-full max-h-full"} />
      </div>
    </div>
  );
}
