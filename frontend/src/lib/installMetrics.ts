// Installation metrics (ADR-075): the installation-wide choice, read and saved through System Settings.

import type { components } from "../../contracts/internal-api";
import { api } from "@/lib/api";

export type InstallMetricsSettings = components["schemas"]["InstallMetricsSettings"];
export type SystemSettings = components["schemas"]["SystemSettingsResponse"];
export type InstallMetricsChoice = components["schemas"]["InstallMetricsChoiceRequest"]["choice"];

// The disclosure version a choice is made against. Held equal to the backend's NOTICE_VERSION
// (app/services/install_metrics/state.py) by backend/tests/test_install_metrics_notice.py.
export const INSTALL_METRICS_NOTICE_VERSION = "2026-11-15";

export const SYSTEM_SETTINGS_PATH = "/api/system-settings";
export const INSTALL_METRICS_PATH = "/api/system-settings/install-metrics";
export const INSTALL_METRICS_NOTICE_URL =
  "https://github.com/bioAF/bioAF/blob/main/docs/installation-metrics.md";

export async function fetchSystemSettings(signal?: AbortSignal): Promise<SystemSettings> {
  return api.get<SystemSettings>(SYSTEM_SETTINGS_PATH, { signal });
}

/** Commit a choice against the notice and revision it was read with; a stale revision is a 409. */
export async function saveInstallMetricsChoice(
  current: Pick<InstallMetricsSettings, "notice_version" | "revision">,
  choice: InstallMetricsChoice,
): Promise<SystemSettings> {
  return api.put<SystemSettings>(INSTALL_METRICS_PATH, {
    choice,
    notice_version: current.notice_version,
    revision: current.revision,
  });
}

export const CHOICE_LABEL: Record<InstallMetricsChoice, string> = {
  enabled: "Share installation metrics",
  disabled: "Do not share",
};

/** A save refused because another admin already chose, or the notice changed: re-read before retrying. */
export function isChoiceConflict(e: unknown): boolean {
  return (e as { status?: number } | null)?.status === 409;
}
