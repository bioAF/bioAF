"use client";

import { INSTALL_METRICS_NOTICE_URL, type InstallMetricsChoice } from "@/lib/installMetrics";

/** The fields a report carries, as listed in docs/installation-metrics.md. */
const REPORT_FIELDS: [string, string][] = [
  ["event", "bioaf_installation_completed, bioaf_installation_observed, or bioaf_upgrade_completed"],
  ["uuid", "a random ID for the report, reused only when a report is retried"],
  ["distinct_id", "the random installation ID"],
  ["timestamp", "the reporting date (UTC), at midnight"],
  ["metrics_schema_version", "1"],
  ["bioaf_version", "the official release version, or unknown"],
  ["from_version", "upgrades only: the previous official release version, or unknown"],
  ["$geoip_disable", "true: no location lookup"],
  ["first_reported_date", "kept once on the installation's profile"],
  ["registration_kind", "kept once: new, existing, or first_seen_on_upgrade"],
];

/**
 * The installation metrics disclosure and its two choices. Neither choice is preselected; the caller
 * owns the value and the save. Shared by the setup wizard, the first-entry prompt and System Settings.
 */
export function InstallMetricsDisclosure({
  value,
  onChange,
  alreadyCompleted,
  name = "install-metrics-choice",
  disabled = false,
}: {
  value: InstallMetricsChoice | null;
  onChange: (choice: InstallMetricsChoice) => void;
  /** Setup already completed: only the current observation date would be reported. */
  alreadyCompleted: boolean;
  name?: string;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-3 text-sm text-gray-700">
      <p>
        Help improve bioAF by sharing optional installation metrics. Reports use a random installation ID to
        connect your installation and successful upgrades over time. They contain the reporting date, bioAF
        release version, and the previous version for upgrades. They do not include names, email addresses,
        organization details, user activity, or scientific data.
      </p>
      <p>
        Reports go to PostHog for the bioAF team. The receiving service sees the server&apos;s network address to
        handle the connection; we configure IP addresses to be discarded from stored metrics and disable location
        enrichment. The public notice explains processing and retention.
      </p>
      <p>
        Reporting starts only if you choose Share installation metrics. Choosing Do not share sends nothing,
        including the initial report. You can change your choice in Settings &gt; System Settings. Turning
        reporting off stops future reports; the privacy notice explains how to request deletion of previous
        reports.
      </p>
      {alreadyCompleted && (
        <p>
          If you enable reporting now, we record that this installation was first observed today. We do not send
          earlier installation or upgrade history.
        </p>
      )}
      <details className="rounded border border-gray-200 px-3 py-2">
        <summary className="cursor-pointer font-medium">What a report contains</summary>
        <dl className="mt-2 space-y-1">
          {REPORT_FIELDS.map(([field, meaning]) => (
            <div key={field} className="flex gap-2">
              <dt className="font-mono text-xs text-gray-900">{field}</dt>
              <dd className="text-xs text-gray-600">{meaning}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-2 text-xs text-gray-600">
          IP addresses are discarded from stored metrics and location enrichment is disabled.
        </p>
      </details>
      <p>
        <a
          href={INSTALL_METRICS_NOTICE_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="text-bioaf-600 underline"
        >
          Read the installation metrics notice
        </a>
      </p>
      <fieldset className="space-y-2" disabled={disabled}>
        <legend className="sr-only">Installation metrics</legend>
        <label className="flex items-center gap-2">
          <input
            type="radio"
            name={name}
            value="enabled"
            checked={value === "enabled"}
            onChange={() => onChange("enabled")}
          />
          Share installation metrics
        </label>
        <label className="flex items-center gap-2">
          <input
            type="radio"
            name={name}
            value="disabled"
            checked={value === "disabled"}
            onChange={() => onChange("disabled")}
          />
          Do not share
        </label>
      </fieldset>
    </div>
  );
}
