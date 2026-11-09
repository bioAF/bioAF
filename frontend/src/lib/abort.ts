/**
 * Cancellation helpers. A leaf module so hooks can recognize a cancelled request without
 * importing the API client (which many tests replace wholesale).
 */

/** Whether `error` is a deliberate cancellation rather than a failure. */
export function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: unknown }).name === "AbortError"
  );
}

export function abortError(): DOMException {
  return new DOMException("The operation was aborted.", "AbortError");
}

/**
 * One signal that aborts when any of `signals` does. `release` detaches the listeners, so a
 * long-lived signal (the session's) does not collect one per finished request.
 */
export function linkSignals(...signals: Array<AbortSignal | undefined>): {
  signal: AbortSignal;
  release: () => void;
} {
  const controller = new AbortController();
  const attached: Array<[AbortSignal, () => void]> = [];
  for (const source of signals) {
    if (!source) continue;
    if (source.aborted) {
      controller.abort();
      break;
    }
    const onAbort = () => controller.abort();
    source.addEventListener("abort", onAbort, { once: true });
    attached.push([source, onAbort]);
  }
  return {
    signal: controller.signal,
    release: () => attached.forEach(([source, onAbort]) => source.removeEventListener("abort", onAbort)),
  };
}
