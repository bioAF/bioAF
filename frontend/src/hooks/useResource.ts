"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isAbortError } from "@/lib/abort";
import { onSessionReset } from "@/lib/session";

/**
 * One consumer's ownership of a keyed read, with optional polling.
 *
 * The key names everything that selects the result (IDs, query, filters, page, sort); `null`
 * disables the read. Every read gets a generation: changing the key, disabling, unmounting, a
 * session ending or starting, a superseding refresh, or a local update invalidates the read in
 * flight and aborts it. A read that settles after it was invalidated changes nothing: not data,
 * not errors, not loading, not callbacks.
 *
 * What the consumer can tell apart:
 * - `loading`: no answer yet for this key (initial load, or a new key). Data from a previous key
 *   is never presented as this key's.
 * - `refreshing`: a read is in flight while this key's last answer is still shown.
 * - `error` with `stale`: the latest read failed; `stale` means the data shown is the last good
 *   answer for this key. An initial failure has `error` and no data, never an empty result.
 *
 * Polling waits for each read to settle before scheduling the next, so reads for one key never
 * overlap. A manual refresh supersedes a poll read in flight, and the timer resumes after it.
 * Retries are left to the next tick; the hook adds none.
 *
 * The feature declares the policy, the hook applies it: `poll` may be a function of this key's
 * current data and error (active vs. terminal, re-check after a failure), and `expected` names
 * failures that are not errors (a 403 for a role without access). An expected failure is reported
 * as `denied`, clears any earlier error, and stops polling until a refresh or a new key.
 *
 * No response is cached or shared between consumers: state lives in the component.
 */

export type ResourceLoader<T> = (signal: AbortSignal) => Promise<T>;

/** What a polling policy sees: this key's current answer and failure. */
export interface ResourceView<T> {
  data: T | undefined;
  error: unknown;
}

export type PollPolicy<T> = number | false | ((current: ResourceView<T>) => number | false);

export interface ResourceOptions<T> {
  /**
   * Milliseconds between the end of one read and the start of the next, or false for none; or a
   * function of the current data and error that returns either.
   */
  poll?: PollPolicy<T>;
  /** Failures that are expected (e.g. permission denied): reported as `denied`, never polled. */
  expected?: (error: unknown) => boolean;
  /** Called with each answer the consumer is shown. */
  onSuccess?: (data: T) => void;
  /** Called with each failure of a current read (cancellation is not a failure). */
  onError?: (error: unknown) => void;
}

interface ResourceState<T> {
  key: string | null;
  data: T | undefined;
  error: unknown;
  denied: boolean;
  inFlight: boolean;
  /** Increments whenever a read settles or the data is replaced; schedules the next poll. */
  settled: number;
}

export interface Resource<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  refreshing: boolean;
  stale: boolean;
  /** The latest read failed in a way the feature declared expected (see `expected`). */
  denied: boolean;
  /** Read again now, superseding any read in flight. Resolves when that read settles. */
  refresh: () => Promise<void>;
  /** Replace the data locally (e.g. after a mutation), superseding any read in flight. */
  mutate: (next: T | ((previous: T | undefined) => T)) => void;
}

export function useResource<T>(
  key: string | null,
  load: ResourceLoader<T>,
  options: ResourceOptions<T> = {},
): Resource<T> {
  const [state, setState] = useState<ResourceState<T>>({
    key: null,
    data: undefined,
    error: null,
    denied: false,
    inFlight: false,
    settled: 0,
  });
  const [sessionEpoch, setSessionEpoch] = useState(0);

  // Held in refs so an inline loader or options object is not a refetch trigger.
  const loadRef = useRef(load);
  const optionsRef = useRef(options);
  useEffect(() => {
    loadRef.current = load;
    optionsRef.current = options;
  });

  // Work is only started while mounted: a refresh or mutate kept past unmount (an awaited
  // mutation finishing after navigation) must not start a read or run a callback.
  const mountedRef = useRef(false);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const keyRef = useRef(key);
  const generationRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const invalidate = useCallback(() => {
    generationRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const run = useCallback(async (): Promise<void> => {
    const k = keyRef.current;
    if (k === null || !mountedRef.current) return;
    invalidate();
    const generation = generationRef.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    setState((s) =>
      s.key === k
        ? { ...s, inFlight: true }
        : { key: k, data: undefined, error: null, denied: false, inFlight: true, settled: s.settled },
    );

    try {
      const data = await loadRef.current(controller.signal);
      if (generation !== generationRef.current) return;
      controllerRef.current = null;
      setState((s) => ({ key: k, data, error: null, denied: false, inFlight: false, settled: s.settled + 1 }));
      optionsRef.current.onSuccess?.(data);
    } catch (error) {
      if (generation !== generationRef.current) return;
      controllerRef.current = null;
      if (isAbortError(error)) {
        setState((s) => ({ ...s, inFlight: false, settled: s.settled + 1 }));
        return;
      }
      if (optionsRef.current.expected?.(error)) {
        setState((s) => ({ key: k, data: undefined, error: null, denied: true, inFlight: false, settled: s.settled + 1 }));
        return;
      }
      setState((s) => ({ ...s, key: k, error, denied: false, inFlight: false, settled: s.settled + 1 }));
      optionsRef.current.onError?.(error);
    }
  }, [invalidate]);

  useEffect(() => {
    keyRef.current = key;
    if (key === null) {
      setState((s) => (s.inFlight ? { ...s, inFlight: false } : s));
      return;
    }
    void run();
    return invalidate;
  }, [key, sessionEpoch, run, invalidate]);

  // A session ending (or another starting) invalidates whatever this consumer holds; the next
  // session reads its own answer.
  useEffect(() => {
    const unsubscribe = onSessionReset(() => {
      invalidate();
      setState((s) => ({ key: null, data: undefined, error: null, denied: false, inFlight: false, settled: s.settled }));
      setSessionEpoch((n) => n + 1);
    });
    return () => {
      unsubscribe();
    };
  }, [invalidate]);

  // The policy sees only this key's settled answer; a denial is never polled.
  const current = key !== null && state.key === key;
  const policy = options.poll ?? false;
  const poll =
    !current || state.denied
      ? false
      : typeof policy === "function"
        ? policy({ data: state.data, error: state.error })
        : policy;
  useEffect(() => {
    if (key === null || poll === false || state.key !== key || state.inFlight) return;
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      void run();
    }, poll);
    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [key, poll, state.key, state.inFlight, state.settled, run]);

  const mutate = useCallback(
    (next: T | ((previous: T | undefined) => T)) => {
      const k = keyRef.current;
      if (k === null || !mountedRef.current) return;
      invalidate();
      setState((s) => ({
        key: k,
        data:
          typeof next === "function"
            ? (next as (previous: T | undefined) => T)(s.key === k ? s.data : undefined)
            : next,
        error: null,
        denied: false,
        inFlight: false,
        settled: s.settled + 1,
      }));
    },
    [invalidate],
  );

  const data = current ? state.data : undefined;
  const error = current ? state.error : null;
  const denied = current && state.denied;
  return {
    data,
    error,
    loading:
      key !== null && (!current || (data === undefined && !denied && (state.inFlight || error === null))),
    refreshing: current && state.inFlight && data !== undefined,
    stale: current && data !== undefined && error !== null,
    denied,
    refresh: run,
    mutate,
  };
}
