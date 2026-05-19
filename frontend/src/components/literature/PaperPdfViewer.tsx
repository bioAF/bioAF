"use client";

import { useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { fetchPaperPdfBlob } from "@/lib/literature";

// pdf.js renders on a web worker. The bundler (Next/webpack) resolves this URL
// to an emitted asset served from our own origin, so there is no CDN dependency
// and nothing for a strict Content-Security-Policy to block.
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

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
  const pdfRef = useRef<PDFDocumentProxy | null>(null);
  const onReachRef = useRef(onReachPage);
  onReachRef.current = onReachPage;

  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Load the document (and a download URL) once per paper.
  useEffect(() => {
    let cancelled = false;
    let createdUrl: string | null = null;
    setLoading(true);
    setError(null);
    setNumPages(0);
    setPage(1);
    pdfRef.current = null;

    fetchPaperPdfBlob(paperId)
      .then(async (blob) => {
        if (cancelled) return;
        createdUrl = URL.createObjectURL(blob);
        setDownloadUrl(createdUrl);
        const data = await blob.arrayBuffer();
        if (cancelled) return;
        const pdf = await pdfjsLib.getDocument({ data }).promise;
        if (cancelled) return;
        pdfRef.current = pdf;
        setNumPages(pdf.numPages);
        setPage(1);
        setLoading(false);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Could not load the PDF.");
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [paperId]);

  // Render the current page whenever it (or the loaded document) changes.
  useEffect(() => {
    const pdf = pdfRef.current;
    if (!pdf || numPages === 0) return;
    let cancelled = false;
    (async () => {
      const pdfPage = await pdf.getPage(page);
      if (cancelled) return;
      const canvas = canvasRef.current;
      if (!canvas) return;
      const viewport = pdfPage.getViewport({ scale: RENDER_SCALE });
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      await pdfPage.render({ canvas, canvasContext: ctx, viewport }).promise;
    })();
    return () => {
      cancelled = true;
    };
  }, [page, numPages]);

  // Report the reached page to the parent on every page change.
  useEffect(() => {
    if (numPages > 0) onReachRef.current?.(page, numPages);
  }, [page, numPages]);

  if (loading) {
    return <div className="text-sm text-gray-400 p-4">Loading PDF...</div>;
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
        className="overflow-auto border border-gray-200 rounded bg-gray-50 flex justify-center"
        style={{ maxHeight: "80vh" }}
      >
        <canvas ref={canvasRef} className="max-w-full" />
      </div>
    </div>
  );
}
