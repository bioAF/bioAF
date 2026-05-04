/**
 * Browser-side chunked PUT against a GCS resumable upload session URL.
 *
 * The session URL is created server-side by ReferenceDataService.init_upload
 * (spec §2). The browser then PUTs the bytes directly — no bioAF auth on the
 * upload path, just GCS's own session token embedded in the URL.
 *
 * Resume protocol: GCS returns 308 with `Range: bytes=0-N` to tell us the
 * highest byte it has accepted. We continue from N+1.
 */

const ONE_GIB = 1024 * 1024 * 1024;
const DEFAULT_CHUNK = 8 * 1024 * 1024;
const LARGE_FILE_CHUNK = 64 * 1024 * 1024;

export function pickChunkSize(totalBytes: number): number {
  return totalBytes > ONE_GIB ? LARGE_FILE_CHUNK : DEFAULT_CHUNK;
}

export interface ResumableUploadOptions {
  chunkSize?: number;
  signal?: AbortSignal;
  onProgress?: (bytesUploaded: number, totalBytes: number) => void;
}

export async function uploadFileResumable(
  sessionUrl: string,
  file: File,
  opts: ResumableUploadOptions = {},
): Promise<void> {
  const total = file.size;
  const chunkSize = opts.chunkSize ?? pickChunkSize(total);
  let start = 0;

  while (start < total) {
    if (opts.signal?.aborted) {
      throw new Error("Upload aborted");
    }
    const end = Math.min(start + chunkSize, total);
    const chunk = file.slice(start, end);
    const response = await fetch(sessionUrl, {
      method: "PUT",
      headers: {
        "Content-Range": `bytes ${start}-${end - 1}/${total}`,
      },
      body: chunk,
      signal: opts.signal,
    });

    if (response.status === 200 || response.status === 201) {
      opts.onProgress?.(total, total);
      return;
    }
    if (response.status === 308) {
      const rangeHeader = response.headers.get("Range");
      if (rangeHeader) {
        // GCS returns "bytes=0-N" — last byte received
        const match = rangeHeader.match(/bytes=\d+-(\d+)/);
        if (match) {
          const lastByte = parseInt(match[1], 10);
          start = lastByte + 1;
          opts.onProgress?.(start, total);
          continue;
        }
      }
      // No Range header: assume the full chunk we just sent landed
      start = end;
      opts.onProgress?.(start, total);
      continue;
    }
    throw new Error(
      `Resumable upload failed: ${response.status} ${response.statusText}`,
    );
  }
}
