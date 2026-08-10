"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { logError, loadFailureMessage } from "@/lib/errorReporting";

/**
 * One widget's data, and the retry that recovers just that widget.
 *
 * Sixteen dashboard widgets offered a Retry that called
 * `window.location.reload()`. Recovering one card that way costs every other
 * card its loaded state, re-runs eighteen requests, and throws away scroll
 * position and any open picker: the most expensive possible response to the
 * cheapest possible failure.
 *
 * They also all did `.catch(() => setError("Failed to load X"))`, which
 * discards the error entirely. These were the last places in the app not
 * following the rule that the screen gets a plain sentence and the log gets the
 * real thing.
 *
 * The fetcher is held in a ref because widgets pass an inline arrow, which is a
 * new function on every render. In the dependency array that is an endless
 * refetch loop.
 */
export function useWidgetData<T>(fetcher: () => Promise<T>, what: string) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const latestFetcher = useRef(fetcher);
  useEffect(() => {
    latestFetcher.current = fetcher;
  });

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);

    latestFetcher
      .current()
      .then((result) => {
        if (!live) return;
        setData(result);
      })
      .catch((e) => {
        if (!live) return;
        logError(`loading ${what} on the dashboard`, e);
        setError(loadFailureMessage(what));
      })
      .finally(() => {
        if (live) setLoading(false);
      });

    return () => {
      live = false;
    };
    // `what` is a literal at every call site; `attempt` is what re-runs this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  return { data, loading, error, retry };
}
