/**
 * Tests for the chunked PUT loop against a GCS resumable session URL.
 *
 * Spec §2 (browser upload, chunked PUT with resume):
 * - PUT each chunk with `Content-Range: bytes {start}-{end-1}/{total}`.
 * - On 308 Resume Incomplete, parse the `Range: bytes=0-N` header to learn
 *   the last byte the server saw and continue from N+1.
 * - On 200/201 the upload is complete.
 * - Chunk size: 8 MiB default; 64 MiB for files > 1 GiB.
 */
import { uploadFileResumable, pickChunkSize } from "./resumableUpload";

function mockBlob(size: number, name = "test.bin"): File {
  const arr = new Uint8Array(size);
  return new File([arr], name, { type: "application/octet-stream" });
}

function fakeResponse(
  status: number,
  headers: Record<string, string> = {},
  statusText = "",
): Response {
  return {
    status,
    statusText,
    headers: {
      get: (name: string) => headers[name] ?? null,
    },
  } as unknown as Response;
}

describe("pickChunkSize", () => {
  it("returns 8 MiB for files <= 1 GiB", () => {
    expect(pickChunkSize(1024)).toBe(8 * 1024 * 1024);
    expect(pickChunkSize(1024 * 1024 * 1024)).toBe(8 * 1024 * 1024);
  });
  it("returns 64 MiB for files > 1 GiB", () => {
    expect(pickChunkSize(2 * 1024 * 1024 * 1024)).toBe(64 * 1024 * 1024);
  });
});

describe("uploadFileResumable", () => {
  let originalFetch: typeof global.fetch;
  beforeEach(() => {
    originalFetch = global.fetch;
  });
  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("PUTs a single chunk for a small file and resolves on 200", async () => {
    const file = mockBlob(1024);
    const calls: { range: string | null; bodySize: number }[] = [];

    global.fetch = jest.fn(async (_url, init) => {
      const headers = init?.headers as Record<string, string>;
      const range = headers["Content-Range"] ?? null;
      const bodyBytes = init?.body instanceof Blob ? init.body.size : 0;
      calls.push({ range, bodySize: bodyBytes });
      return fakeResponse(200);
    }) as jest.Mock;

    await uploadFileResumable("https://gcs.example/session", file);

    expect(calls).toHaveLength(1);
    expect(calls[0].range).toBe("bytes 0-1023/1024");
    expect(calls[0].bodySize).toBe(1024);
  });

  it("chunks a large file across multiple PUTs, advancing on 308 with Range header", async () => {
    const total = 20 * 1024; // 20 KB
    const chunkSize = 8 * 1024; // 8 KB
    const file = mockBlob(total);

    const ranges: string[] = [];
    let call = 0;
    global.fetch = jest.fn(async (_url, init) => {
      const headers = init?.headers as Record<string, string>;
      ranges.push(headers["Content-Range"]);
      call++;
      // First two chunks: 308 with Range advancing to last byte received
      if (call < 3) {
        const lastByte = call * chunkSize - 1;
        return fakeResponse(308, { Range: `bytes=0-${lastByte}` });
      }
      return fakeResponse(200);
    }) as jest.Mock;

    const progress: number[] = [];
    await uploadFileResumable("https://gcs.example/session", file, {
      chunkSize,
      onProgress: (uploaded) => progress.push(uploaded),
    });

    expect(ranges).toEqual([
      `bytes 0-${chunkSize - 1}/${total}`,
      `bytes ${chunkSize}-${chunkSize * 2 - 1}/${total}`,
      `bytes ${chunkSize * 2}-${total - 1}/${total}`,
    ]);
    expect(progress[progress.length - 1]).toBe(total);
  });

  it("respects abort signal and stops issuing further chunks", async () => {
    const file = mockBlob(20 * 1024);
    const ctrl = new AbortController();
    let chunkCount = 0;
    global.fetch = jest.fn(async (_url, init) => {
      chunkCount++;
      if (chunkCount === 1) {
        ctrl.abort();
        return fakeResponse(308, { Range: "bytes=0-8191" });
      }
      return fakeResponse(200);
    }) as jest.Mock;

    await expect(
      uploadFileResumable("https://gcs.example/session", file, {
        chunkSize: 8 * 1024,
        signal: ctrl.signal,
      }),
    ).rejects.toThrow(/aborted/i);
    expect(chunkCount).toBe(1);
  });

  it("throws if GCS returns a 4xx/5xx", async () => {
    const file = mockBlob(1024);
    global.fetch = jest.fn(async () => fakeResponse(403, {}, "Forbidden")) as jest.Mock;

    await expect(uploadFileResumable("https://gcs.example/session", file)).rejects.toThrow(
      /403/,
    );
  });
});
