import { useEffect, useState } from "react";

/**
 * Returns a debounced copy of `value` that only updates after `value` has been
 * stable for `delayMs`. Used so the header search waits for the user to pause
 * typing before firing a request.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}
