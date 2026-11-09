/**
 * Full-page navigation to the login page. A leaf module so the API client can leave an expired
 * session without importing the router, and so tests can observe the redirect (jsdom cannot
 * navigate).
 */
export function navigateToLogin(): void {
  if (typeof window !== "undefined") {
    window.location.href = "/login";
  }
}
