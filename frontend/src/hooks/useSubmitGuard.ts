import { useCallback, useRef, useState } from "react";

/**
 * One submission at a time for a form.
 *
 * `busy` is for display (the button's busy label) and only changes on the next
 * render, so on its own it lets a second Enter or double click that lands
 * before that render submit again. The ref is checked and set synchronously,
 * before the first await, and released in `finally` so a failure can be
 * retried.
 *
 * Each accepted run owns the guard until it settles. `reset` ends that
 * ownership when the feature's dialog, entity, or session ends: the guard and
 * `busy` are released at once, and the old attempt settling later cannot
 * release a newer one. Reset does not cancel the work itself; aborting requests
 * and ignoring their late results stays with the feature.
 */
export function useSubmitGuard() {
  const owner = useRef<object | null>(null);
  const [busy, setBusy] = useState(false);

  const run = useCallback(async (work: () => Promise<void>) => {
    if (owner.current) return;
    const attempt = {};
    owner.current = attempt;
    setBusy(true);
    try {
      await work();
    } finally {
      if (owner.current === attempt) {
        owner.current = null;
        setBusy(false);
      }
    }
  }, []);

  const reset = useCallback(() => {
    owner.current = null;
    setBusy(false);
  }, []);

  return { busy, run, reset };
}
