/**
 * Watch a server job until it settles, a deadline passes, or the watcher stops.
 *
 * Reads are serialized: the next starts `intervalMs` after the previous one settles. Stopping
 * (aborting `signal`) ends the watch at once, including during the wait, and rejects with an
 * AbortError so the caller runs no completion code. Stopping the watch never cancels the job.
 * A leaf module (no API client import) so tests that replace the client keep working.
 */
import { abortError } from "./abort";

export interface WatchOptions {
  intervalMs: number;
  /** Milliseconds from the start of the watch after which it gives up with `timedOut`. */
  deadlineMs?: number;
  /** The most reads to make before giving up with `timedOut`. */
  maxReads?: number;
  signal?: AbortSignal;
}

export interface WatchResult<T> {
  value: T;
  /** The deadline passed while the job was still running; `value` is its last known state. */
  timedOut: boolean;
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export async function watchJob<T>(
  initial: T,
  read: (signal?: AbortSignal) => Promise<T>,
  settled: (value: T) => boolean,
  options: WatchOptions,
): Promise<WatchResult<T>> {
  const { intervalMs, deadlineMs, maxReads = Infinity, signal } = options;
  const deadline = deadlineMs === undefined ? Infinity : Date.now() + deadlineMs;
  let current = initial;
  let reads = 0;
  while (!settled(current)) {
    if (signal?.aborted) throw abortError();
    if (Date.now() > deadline || reads >= maxReads) return { value: current, timedOut: true };
    await wait(intervalMs, signal);
    reads += 1;
    current = await read(signal);
    if (signal?.aborted) throw abortError();
  }
  return { value: current, timedOut: false };
}
