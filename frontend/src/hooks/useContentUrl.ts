"use client";

import { useEffect, useState } from "react";
import { fileContentUrl, plotThumbnailContentUrl } from "@/lib/api";

/**
 * Fetch a short-lived content URL for inline display in <img> tags.
 * Returns null while loading, then the URL string once the content
 * token has been issued.
 */
export function useFileContentUrl(fileId: number | null): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (fileId == null) {
      setUrl(null);
      return;
    }
    let cancelled = false;
    fileContentUrl(fileId).then((u) => {
      if (!cancelled) setUrl(u);
    });
    return () => {
      cancelled = true;
    };
  }, [fileId]);

  return url;
}

export function usePlotThumbnailContentUrl(
  plotId: number | null,
): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (plotId == null) {
      setUrl(null);
      return;
    }
    let cancelled = false;
    plotThumbnailContentUrl(plotId).then((u) => {
      if (!cancelled) setUrl(u);
    });
    return () => {
      cancelled = true;
    };
  }, [plotId]);

  return url;
}
