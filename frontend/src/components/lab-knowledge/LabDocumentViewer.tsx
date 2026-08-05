"use client";

import { useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { fetchLabDocumentBlob } from "@/lib/labDocuments";

// pdf.js renders on a web worker; the bundler resolves this URL to an asset on
// our own origin (no CDN), so a strict CSP won't block it.
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

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
  const pdfRef = useRef<PDFDocumentProxy | null>(null);

  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [textContent, setTextContent] = useState<string | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let createdUrl: string | null = null;
    setLoading(true);
    setError(null);
    setNumPages(0);
    setPage(1);
    setTextContent(null);
    pdfRef.current = null;

    fetchLabDocumentBlob(documentId, version)
      .then(async (blob) => {
        if (cancelled) return;
        createdUrl = URL.createObjectURL(blob);
        setDownloadUrl(createdUrl);
        if (kind === "pdf") {
          const data = await blob.arrayBuffer();
          if (cancelled) return;
          const pdf = await pdfjsLib.getDocument({ data }).promise;
          if (cancelled) return;
          pdfRef.current = pdf;
          setNumPages(pdf.numPages);
          setPage(1);
        } else if (kind === "text") {
          const text = await blob.text();
          if (cancelled) return;
          setTextContent(text);
        }
        setLoading(false);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Could not load the document.");
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [documentId, version, kind]);

  // Render the current PDF page whenever it (or the loaded document) changes.
  useEffect(() => {
    if (kind !== "pdf") return;
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
  }, [page, numPages, kind]);

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
        <canvas ref={canvasRef} className="max-w-full max-h-full" />
      </div>
    </div>
  );
}
