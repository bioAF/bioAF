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
