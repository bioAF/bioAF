import { useCallback, useRef, useState } from "react";

/**
 * One submission at a time for a form.
 *
 * `busy` is for display (the button's busy label) and only changes on the next
 * render, so on its own it lets a second Enter or double click that lands
 * before that render submit again. The ref is checked and set synchronously,
 * before the first await, and released in `finally` so a failure can be
 * retried.
 */
export function useSubmitGuard() {
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);

  const run = useCallback(async (work: () => Promise<void>) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await work();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }, []);

  return { busy, run };
}
