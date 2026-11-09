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
  if (!path.startsWith("/") || path.startsWith("//")) {
    throw new Error(`Not an application API path: ${JSON.stringify(path)}`);
  }
  return `${apiBaseUrl()}${path}`;
}
