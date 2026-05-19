"use client";

import { useEffect, useState } from "react";
import { fetchPaperPdfObjectUrl } from "@/lib/literature";

interface Props {
  paperId: number;
  filename?: string;
}

// Renders the paper's PDF inline using the browser's native viewer. The PDF
// endpoint is authenticated, so we fetch the bytes as a blob (with the Bearer
// token) and hand the object URL to an <iframe>. The same URL backs the
// Download link. The object URL is revoked on unmount or when the paper
// changes.
export function PaperPdfViewer({ paperId, filename }: Props) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let createdUrl: string | null = null;
    setLoading(true);
    setError(null);
    fetchPaperPdfObjectUrl(paperId)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        createdUrl = url;
        setObjectUrl(url);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Could not load the PDF.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [paperId]);

  if (loading) {
    return (
      <div className="text-sm text-gray-400 p-4">Loading PDF...</div>
    );
  }

  if (error) {
    return <div className="text-sm text-red-700 p-4">{error}</div>;
  }

  if (!objectUrl) return null;

  return (
    <div>
      <div className="flex justify-end mb-2">
        <a
          href={objectUrl}
          download={filename ?? "paper.pdf"}
          className="text-bioaf-700 hover:underline text-sm"
        >
          Download PDF
        </a>
      </div>
      <iframe
        title="Paper PDF"
        src={objectUrl}
        className="w-full rounded border border-gray-200"
        style={{ height: "80vh" }}
      />
    </div>
  );
}
