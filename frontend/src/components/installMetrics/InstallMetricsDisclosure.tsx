"use client";

import { INSTALL_METRICS_NOTICE_URL, type InstallMetricsChoice } from "@/lib/installMetrics";

/**
 * The short installation metrics explanation, with the detail behind "Learn more" and the exact fields,
 * processing, retention and deletion in the linked public notice (docs/installation-metrics.md).
 */
export function InstallMetricsSummary() {
  return (
    <div className="space-y-3 text-sm text-gray-700">
      <p>Share basic installation and update information to help guide bioAF&rsquo;s development.</p>
      <p>
        Reports include a random installation ID, reporting dates, and bioAF version numbers. Names, lab details,
        user activity, and research data are never included.
      </p>
      <p>Nothing is sent unless you agree. You can change your choice anytime in Settings &gt; System Settings.</p>
      <details>
        <summary className="cursor-pointer text-bioaf-600">Learn more about installation metrics</summary>
        <div className="mt-2 space-y-2">
          <p>
            bioAF collects a unique identifier for each installation and tracks installs, updates, and activated
            features. It does not track any identifying information about your organization, your users, your
            experiments, or any scientific data. This tells us how many installations exist in the wild and which
            features provide the most value to our users, so we can direct our engineering efforts.
          </p>
          <p>
            <a
              href={INSTALL_METRICS_NOTICE_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-bioaf-600 underline"
            >
              Read the full installation metrics notice
            </a>
          </p>
        </div>
      </details>
    </div>
  );
}

/**
 * The explanation with the two choices, neither preselected; the caller owns the value and the save.
 * Used by the setup wizard and System Settings.
 */
export function InstallMetricsDisclosure({
  value,
  onChange,
  name = "install-metrics-choice",
  disabled = false,
}: {
  value: InstallMetricsChoice | null;
  onChange: (choice: InstallMetricsChoice) => void;
  name?: string;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-3 text-sm text-gray-700">
      <InstallMetricsSummary />
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
