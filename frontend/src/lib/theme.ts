/**
 * Theme model for the app-wide light/dark switch.
 *
 * A user's choice is one of `light` | `dark` | `system`. `system` (the default
 * before any explicit choice) follows the OS `prefers-color-scheme`. The resolved
 * theme is always concrete (`light` | `dark`) and is applied by toggling the `dark`
 * class on <html>, which the Tailwind `class` dark strategy and the override layer
 * in globals.css key off. Every function here is defensive: localStorage/matchMedia
 * can throw or be absent (private mode, SSR, old engines), and theming must never
 * break the app.
 */

export const THEME_STORAGE_KEY = "bioaf-theme";

export type ThemeChoice = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const CHOICES: ReadonlySet<string> = new Set(["light", "dark", "system"]);

/** Concrete theme for a choice, given the current OS dark preference. */
export function resolveTheme(
  choice: ThemeChoice,
  systemDark: boolean,
): ResolvedTheme {
  if (choice === "light" || choice === "dark") return choice;
  return systemDark ? "dark" : "light";
}

/** True when the OS prefers a dark color scheme; false if unknown/unavailable. */
export function systemPrefersDark(): boolean {
  try {
    return (
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches
    );
  } catch {
    return false;
  }
}

/** The persisted choice, defaulting to `system` for missing/unknown/unreadable. */
export function getStoredTheme(): ThemeChoice {
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (raw && CHOICES.has(raw)) return raw as ThemeChoice;
  } catch {
    // localStorage can throw (disabled, private mode). Fall through to default.
  }
  return "system";
}

/** Persist the choice, swallowing storage errors. */
export function storeTheme(choice: ThemeChoice): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, choice);
  } catch {
    // Non-fatal: the in-memory provider state still drives this session.
  }
}

/** Apply a concrete theme to the document root. */
export function applyResolvedTheme(resolved: ResolvedTheme): void {
  const root = document.documentElement;
  if (resolved === "dark") root.classList.add("dark");
  else root.classList.remove("dark");
  root.style.colorScheme = resolved;
}

/**
 * Inline, self-contained script injected into <head> so the resolved theme is set
 * before first paint (no flash of the wrong theme). Mirrors the logic above in a
 * form safe to run before the bundle loads. Kept in sync with resolveTheme/getStoredTheme.
 */
export const THEME_INIT_SCRIPT = `(function(){try{var k=${JSON.stringify(
  THEME_STORAGE_KEY,
)};/* localStorage can throw in a blocked-cookie context; falling back to the
   system preference is the whole point, and no UI exists yet to report into. */
var c=null;try{c=localStorage.getItem(k);}catch(e){}if(c!=="light"&&c!=="dark"){c=(window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches)?"dark":"light";}var r=document.documentElement;if(c==="dark"){r.classList.add("dark");}else{r.classList.remove("dark");}r.style.colorScheme=c;}catch(e){}})();`;
