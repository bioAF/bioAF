"use client";

// Shared plot-archive thumbnail. Renders an image preview from the plot's
// thumbnail (PDFs) or file content (images), a file-type icon when no preview
// exists, and a placeholder for storage-deleted files. Used by the Plot
// Archive page and the Experiment Results tab so previews behave identically.

import { useState } from "react";
import { useFileContentUrl, usePlotThumbnailContentUrl } from "@/hooks/useContentUrl";
import type { PlotArchiveResponse } from "@/lib/types";

export function PlotThumbnail({
  plot,
  onClick,
}: {
  plot: PlotArchiveResponse;
  onClick: () => void;
}) {
  const [error, setError] = useState(false);
  const fileType = plot.file?.file_type?.toLowerCase() ?? "";
  const isPdf = fileType === "pdf";
  const hasThumbnail = !!plot.thumbnail_url;

  // Hooks must be called unconditionally (before any early returns)
  const thumbnailUrl = usePlotThumbnailContentUrl(isPdf && hasThumbnail ? plot.id : null);
  const fileUrl = useFileContentUrl(!isPdf || !hasThumbnail ? (plot.file?.id ?? null) : null);
  const imgUrl = (isPdf && hasThumbnail ? thumbnailUrl : fileUrl) ?? "";

  // For PDFs without a generated thumbnail, show file-type icon
  if (isPdf && !hasThumbnail) {
    return (
      <button
        type="button"
        className="flex flex-col items-center gap-2 py-6 cursor-pointer hover:opacity-80"
        onClick={onClick}
      >
        <div className="w-16 h-16 bg-gray-200 rounded-lg flex items-center justify-center text-gray-500 text-xs font-bold uppercase">
          PDF
        </div>
        <span className="text-xs text-gray-400">No preview available</span>
      </button>
    );
  }

  if (error) {
    return (
      <button
        type="button"
        className="flex flex-col items-center gap-2 py-6 cursor-pointer hover:opacity-80"
        onClick={onClick}
      >
        <div className="w-16 h-16 bg-gray-200 rounded-lg flex items-center justify-center text-gray-500 text-xs font-bold uppercase">
          {fileType || "?"}
        </div>
        <span className="text-xs text-gray-400">No preview available</span>
      </button>
    );
  }

  // Content-token fetch is still in flight. Don't render <img src=""> because
  // that fires onError immediately and leaves us stuck in the error state.
  if (!imgUrl) {
    return <div className="w-full h-full bg-gray-100 animate-pulse" />;
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={imgUrl}
      alt={plot.title ?? "Plot"}
      className="w-full h-full object-cover cursor-pointer"
      onClick={onClick}
      onError={() => setError(true)}
    />
  );
}

export function StorageDeletedPlaceholder() {
  return (
    <div className="flex flex-col items-center justify-center gap-1.5 text-center px-2">
      <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-red-100 text-red-600">
        <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
          <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
        </svg>
      </span>
      <span className="text-gray-400 text-xs">Storage deleted</span>
    </div>
  );
}
