import type { BootstrapStatus } from "@/lib/types";

/**
 * Where a visitor belongs while setup may be unfinished (project 12A, approved D3). One decision for
 * /login, /setup and the app shell, from the server's status, whether there is a session, and the
 * session's effective permission (null while it is not known):
 *
 * - no administrator, setup incomplete: the first-run wizard (`setup-initial`);
 * - administrator, setup incomplete: sign in (`login`), then review saved setup if permitted
 *   (`setup-resume`), otherwise a plain notice (`setup-unauthorized`);
 * - setup complete: the app when signed in, otherwise sign in.
 */
export type SetupDestination = "setup-initial" | "login" | "setup-resume" | "setup-unauthorized" | "app";

export function setupDestination(
  status: Pick<BootstrapStatus, "has_admin" | "setup_complete">,
  signedIn: boolean,
  canConfigure: boolean | null,
): SetupDestination {
  if (status.setup_complete) return signedIn ? "app" : "login";
  if (!status.has_admin) return "setup-initial";
  if (!signedIn) return "login";
  return canConfigure ? "setup-resume" : "setup-unauthorized";
}

/** The permission that may review and finish an unfinished setup. */
export const SETUP_PERMISSION = { resource: "infrastructure", action: "configure" } as const;
