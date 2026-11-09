/**
 * Application API URL resolution, in one place. A leaf module so the separate unauthenticated
 * transports (setup, readiness) resolve URLs exactly as the API client does without importing it.
 */

/**
 * The application API's base URL. An explicitly empty value means the same origin; unset means
 * the local backend. Read at call time so every caller resolves it the same way.
 */
export function apiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
}

/**
 * The full URL for an application path. Only application-relative paths are accepted, so the
 * authenticated client can never send the session token to another destination.
 */
export function apiUrl(path: string): string {
  if (!isApplicationPath(path)) {
    throw new Error(`Not an application API path: ${JSON.stringify(path)}`);
  }
  return `${apiBaseUrl()}${path}`;
}

// Browsers strip tab, newline, and other control characters from URLs and read a backslash as a
// slash, so "/\host" or "/<tab>/host" would leave the application's origin.
// eslint-disable-next-line no-control-regex
const UNSAFE_CHARACTERS = /[\u0000-\u001f\u007f\\]/;
const SENTINEL_ORIGIN = "https://application.invalid";

function isApplicationPath(path: string): boolean {
  if (!path.startsWith("/") || path.startsWith("//")) return false;
  // Only the path can move the request elsewhere; a query or fragment may carry user text.
  const beforeQuery = path.split(/[?#]/, 1)[0];
  if (UNSAFE_CHARACTERS.test(beforeQuery)) return false;
  // Resolve it the way a browser would and require the origin to be unchanged.
  try {
    return new URL(path, SENTINEL_ORIGIN).origin === SENTINEL_ORIGIN;
  } catch {
    return false;
  }
}
