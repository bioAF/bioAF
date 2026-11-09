/**
 * The application session's lifetime (plan 13).
 *
 * A session starts at login (or setup's first admin) and ends at logout or when the active
 * session's credential is refused. Re-issuing the token inside a session (a profile change) is
 * not a new session. Each session has a generation number and an AbortSignal: work started in a
 * session captures both, and anything that settles after the session ended, or after another one
 * started, must not write into shared state.
 *
 * A leaf module: it imports only token storage and the leaf cache holders, so the API client and
 * the hooks can both depend on it without an import cycle. Caches that live in modules importing
 * the API client register their reset with `onSessionReset` instead.
 */
import { getToken, removeToken, setToken } from "./auth";
import { navigateToLogin } from "./navigation";
import { clearPermissionsCache } from "@/hooks/permissionsCache";
import { clearCapabilitiesCache } from "@/hooks/capabilitiesCache";

let generation = 0;
let controller = new AbortController();
const resetters = new Set<() => void>();

/** Changes whenever a session starts or ends. Capture it before a request; compare after. */
export function sessionGeneration(): number {
  return generation;
}

/** Aborted when the current session ends or is replaced by another. */
export function sessionSignal(): AbortSignal {
  return controller.signal;
}

/** Register a session-bound holder's reset. Returns an unsubscribe function. */
export function onSessionReset(reset: () => void): () => void {
  resetters.add(reset);
  return () => resetters.delete(reset);
}

function resetSessionState(): void {
  generation += 1;
  controller.abort();
  controller = new AbortController();
  clearPermissionsCache();
  clearCapabilitiesCache();
  resetters.forEach((reset) => reset());
}

/** Begin a new session with this token. Nothing from an earlier session carries into it. */
export function startSession(token: string): void {
  resetSessionState();
  setToken(token);
}

/** The same session, re-issued credential (e.g. after a profile change). Holders survive. */
export function replaceSessionToken(token: string): void {
  setToken(token);
}

/** End the session: logout and expiry both come here. Safe to call more than once. */
export function endSession(): void {
  removeToken();
  resetSessionState();
}

/**
 * A request sent with `sentToken` was refused with 401. End the session only when that token is
 * still the session's credential: a refusal of a credential that has been replaced, or of a
 * request from an earlier session, says nothing about the current one. A request sent with no
 * token (the login form) is not a session expiry either.
 */
export function expireSession(sentToken: string | null): boolean {
  if (!sentToken || sentToken !== getToken()) return false;
  endSession();
  navigateToLogin();
  return true;
}
