/**
 * Records each mocked read's URL and AbortSignal, leaving every read pending, so a test can
 * check that a list read is cancelled when its key changes or its page unmounts.
 */
export type CapturedRead = { url: string; signal: AbortSignal | undefined };

export function captureReads(mock: jest.Mock) {
  const reads: CapturedRead[] = [];
  mock.mockImplementation((url: unknown, options?: { signal?: AbortSignal }) => {
    reads.push({ url: String(url), signal: options?.signal });
    return new Promise(() => {});
  });
  return {
    reads,
    /** The newest read whose URL contains `fragment`. */
    latest(fragment: string): CapturedRead {
      const match = [...reads].reverse().find((r) => r.url.includes(fragment));
      if (!match) throw new Error(`no read matching ${fragment}: ${reads.map((r) => r.url)}`);
      return match;
    },
  };
}
