"use client";

import { useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import type { PDFDocumentProxy, PageViewport } from "pdfjs-dist";
import { fetchLabDocumentBlob } from "@/lib/labDocuments";
import { createPdfEngine, type OwnedPdfWorker } from "@/lib/pdfSession";
import { pageFailureMessage, usePdfDocument } from "@/hooks/usePdfDocument";

// pdf.js renders on a web worker; the bundler resolves this URL to an asset on
// our own origin (no CDN), so a strict CSP won't block it.
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

// Each document gets its own worker so a stuck one can be terminated
// (src/lib/pdfSession.ts).
const ENGINE = createPdfEngine<PDFDocumentProxy, pdfjsLib.PDFWorker>(pdfjsLib);

interface Props {
  documentId: number;
  version?: number;
  mimeType: string | null;
  fileName: string;
}

const RENDER_SCALE = 1.4;

type Kind = "pdf" | "image" | "text" | "other";

function kindFor(mimeType: string | null, fileName: string): Kind {
  const mt = (mimeType ?? "").toLowerCase();
  if (mt === "application/pdf" || fileName.toLowerCase().endsWith(".pdf")) return "pdf";
  if (mt.startsWith("image/")) return "image";
  if (mt.startsWith("text/") || mt === "application/json") return "text";
  return "other";
}

// Reads a lab document's bytes through the backend and renders them inline:
// paginated PDF (pdf.js), images, and plain text get a real preview; anything
// else falls back to a download link. Mirrors the literature PaperPdfViewer.
export function LabDocumentViewer({ documentId, version, mimeType, fileName }: Props) {
  const kind = kindFor(mimeType, fileName);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [textContent, setTextContent] = useState<string | null>(null);
  const [pdfData, setPdfData] = useState<ArrayBuffer | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [fetching, setFetching] = useState(true);

  // Fetch the bytes (and a download URL) once per document version.
  useEffect(() => {
    let cancelled = false;
    let createdUrl: string | null = null;
    setFetching(true);
    setFetchError(null);
    setTextContent(null);
    setPdfData(null);

    fetchLabDocumentBlob(documentId, version)
      .then(async (blob) => {
        if (cancelled) return;
        createdUrl = URL.createObjectURL(blob);
        setDownloadUrl(createdUrl);
        if (kind === "pdf") {
          const data = await blob.arrayBuffer();
          if (cancelled) return;
          setPdfData(data);
        } else if (kind === "text") {
          const text = await blob.text();
          if (cancelled) return;
          setTextContent(text);
        }
        setFetching(false);
      })
      .catch((e) => {
        if (!cancelled) {
          setFetchError(e instanceof Error ? e.message : "Could not load the document.");
          setFetching(false);
        }
      });

    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [documentId, version, kind]);

  const pdf = usePdfDocument<PageViewport, PDFDocumentProxy, OwnedPdfWorker<pdfjsLib.PDFWorker>>(
    ENGINE,
    pdfData,
    canvasRef,
    RENDER_SCALE,
    "a lab document PDF",
  );
  const { numPages, page, setPage, pageFailed } = pdf;
  const error = fetchError ?? pdf.error;
  const loading = !error && (fetching || pdf.opening);

  if (loading) {
    return <div className="text-sm text-gray-500 p-4">Loading document...</div>;
  }
  if (error) {
    return <div className="text-sm text-red-700 p-4">{error}</div>;
  }

  const downloadLink = downloadUrl && (
    <a
      href={downloadUrl}
      download={fileName}
      className="text-bioaf-700 hover:underline text-sm"
    >
      Download
    </a>
  );

  if (kind === "image") {
    return (
      <div>
        <div className="flex justify-end mb-2">{downloadLink}</div>
        <div className="border border-gray-200 rounded bg-gray-50 flex items-center justify-center p-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={downloadUrl ?? ""} alt={fileName} className="max-w-full max-h-[85vh]" />
        </div>
      </div>
    );
  }

  if (kind === "text") {
    return (
      <div>
        <div className="flex justify-end mb-2">{downloadLink}</div>
        <pre
          className="border border-gray-200 rounded bg-gray-50 p-3 text-xs overflow-auto whitespace-pre-wrap"
          style={{ maxHeight: "85vh" }}
        >
          {textContent}
        </pre>
      </div>
    );
  }

  if (kind === "other") {
    return (
      <div className="border border-gray-200 rounded bg-gray-50 p-6 text-center text-sm text-gray-600">
        <p className="mb-3">Inline preview isn&apos;t available for this file type.</p>
        {downloadLink}
      </div>
    );
  }

  // PDF
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
        {downloadLink}
      </div>
      <div
        className="overflow-auto border border-gray-200 rounded bg-gray-50 flex items-center justify-center"
        style={{ height: "85vh" }}
      >
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
