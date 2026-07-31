"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  type ResolvedTheme,
  type ThemeChoice,
  applyResolvedTheme,
  getStoredTheme,
  resolveTheme,
  storeTheme,
  systemPrefersDark,
} from "@/lib/theme";

interface ThemeContextValue {
  /** The user's stored preference: light | dark | system. */
  choice: ThemeChoice;
  /** The concrete theme currently applied. */
  resolvedTheme: ResolvedTheme;
  /** Set an explicit preference. */
  setTheme: (choice: ThemeChoice) => void;
  /** Flip between light and dark relative to what is currently shown. */
  toggle: () => void;
}

// A safe light default so a component that reads the theme outside a provider
// (e.g. the shared Header in an isolated unit test) never crashes. In the app the
// root layout always wraps everything in ThemeProvider, so the real value is used.
const DEFAULT_THEME: ThemeContextValue = {
  choice: "system",
  resolvedTheme: "light",
  setTheme: () => {},
  toggle: () => {},
};

const ThemeContext = createContext<ThemeContextValue>(DEFAULT_THEME);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  // Default to `system`/light for the server render; the no-flash init script in
  // <head> has already set the correct class, and the mount effect below syncs
  // React state to the stored preference without a hydration mismatch.
  const [choice, setChoice] = useState<ThemeChoice>("system");
  const [systemDark, setSystemDark] = useState(false);

  useEffect(() => {
    setChoice(getStoredTheme());
    setSystemDark(systemPrefersDark());
  }, []);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setSystemDark(mq.matches);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);

  const resolvedTheme = resolveTheme(choice, systemDark);

  useEffect(() => {
    applyResolvedTheme(resolvedTheme);
  }, [resolvedTheme]);

  const setTheme = useCallback((next: ThemeChoice) => {
    setChoice(next);
    storeTheme(next);
  }, []);

  const toggle = useCallback(() => {
    setChoice((prev) => {
      // Resolve against the live OS preference so a `system` user toggles away
      // from whatever is actually on screen, not a stale snapshot.
      const shown = resolveTheme(prev, systemPrefersDark());
      const next: ThemeChoice = shown === "dark" ? "light" : "dark";
      storeTheme(next);
      return next;
    });
  }, []);

  const value = useMemo(
    () => ({ choice, resolvedTheme, setTheme, toggle }),
    [choice, resolvedTheme, setTheme, toggle],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}
