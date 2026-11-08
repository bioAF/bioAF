/**
 * Whether the backend is in maintenance: finishing a database upgrade, or stopped because one
 * failed (plan 9). While it is, every API request answers 503 with `{"status": "maintenance"}`.
 *
 * A module-level store rather than React state, because the API client (plain code) is what sees
 * the answer and the shell (React) is what shows it. Reporting also drops the tab's cached
 * readiness, so a reload or later navigation checks again instead of trusting it.
 */
export interface MaintenanceInfo {
  status?: string;
  reason?: string | null;
  detail?: string;
  upgrade?: { status?: string; step?: string; error?: string };
}

/** The sessionStorage key useBackendReady caches readiness under. */
export const BACKEND_READY_KEY = "bioaf_backend_ready";

type Listener = (info: MaintenanceInfo | null) => void;

let current: MaintenanceInfo | null = null;
const listeners = new Set<Listener>();

export function isMaintenanceBody(body: unknown): body is MaintenanceInfo {
  return typeof body === "object" && body !== null && (body as MaintenanceInfo).status === "maintenance";
}

export function reportMaintenance(info: MaintenanceInfo): void {
  current = info;
  try {
    sessionStorage.removeItem(BACKEND_READY_KEY);
  } catch {
    // Storage can be unavailable (private mode); the in-memory state still drives the UI.
  }
  listeners.forEach((listener) => listener(info));
}

export function clearMaintenance(): void {
  if (current === null) return;
  current = null;
  listeners.forEach((listener) => listener(null));
}

export function getMaintenance(): MaintenanceInfo | null {
  return current;
}

export function subscribeMaintenance(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
