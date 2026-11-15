"use client";

import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { InstallMetricsDisclosure } from "@/components/installMetrics/InstallMetricsDisclosure";
import { logError } from "@/lib/errorReporting";
import {
  fetchSystemSettings,
  isChoiceConflict,
  saveInstallMetricsChoice,
  type InstallMetricsChoice,
  type InstallMetricsSettings,
} from "@/lib/installMetrics";

function storedChoice(metrics: InstallMetricsSettings): InstallMetricsChoice | null {
  return metrics.preference === "pending" ? null : metrics.preference;
}

/** Installation-wide settings (ADR-075): the installation metrics choice. */
export default function SystemSettingsPage() {
  const [metrics, setMetrics] = useState<InstallMetricsSettings | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [choice, setChoice] = useState<InstallMetricsChoice | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const show = (next: InstallMetricsSettings) => {
    setMetrics(next);
    setChoice(storedChoice(next));
  };

  const load = useCallback(async () => {
    try {
      show((await fetchSystemSettings()).install_metrics);
      setLoadFailed(false);
    } catch (e) {
      logError("loading the system settings", e);
      setLoadFailed(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    if (!metrics || !choice) return;
    setSaving(true);
    setMessage("");
    setError("");
    try {
      const saved = (await saveInstallMetricsChoice(metrics, choice)).install_metrics;
      show(saved);
      setMessage(
        choice === "enabled"
          ? "Installation metrics will be shared."
          : "Installation metrics are no longer shared. Pending reports were cancelled.",
      );
    } catch (e) {
      if (isChoiceConflict(e)) {
        setError("This setting was changed by another administrator. The current choice is shown below.");
        await load();
      } else {
        logError("saving the installation metrics choice", e);
        setError("Your choice was not saved. Try again.");
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <h1 className="text-2xl font-bold mb-2">System Settings</h1>
      <p data-testid="page-description" className="text-sm text-gray-500 mb-6">
        Settings that apply to this whole bioAF installation, including whether it shares installation metrics.
      </p>

      {loadFailed && (
        <p role="alert" className="text-sm text-red-700">
          System settings could not be loaded. The technical detail is in the application logs.
        </p>
      )}

      {metrics && (
        <Card className="max-w-3xl space-y-4 p-6">
          <h2 className="text-lg font-semibold">Installation metrics</h2>
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-gray-500">Status</dt>
            <dd>{metrics.effective ? "Reporting is on." : "Reporting is off."}</dd>
            <dt className="text-gray-500">Choice</dt>
            <dd>
              {metrics.preference === "pending"
                ? "No choice has been made yet."
                : metrics.preference === "enabled"
                  ? "Share installation metrics"
                  : "Do not share"}
            </dd>
            {metrics.installation_id && (
              <>
                <dt className="text-gray-500">Installation ID</dt>
                <dd className="font-mono text-xs">{metrics.installation_id}</dd>
              </>
            )}
          </dl>
          {metrics.operator_override && (
            <p className="rounded bg-amber-50 px-3 py-2 text-sm text-amber-900">
              The server operator turned reporting off (BIOAF_INSTALL_METRICS_DISABLED). Nothing is reported,
              whatever is chosen here.
            </p>
          )}
          <InstallMetricsDisclosure
            value={choice}
            onChange={setChoice}
            alreadyCompleted={metrics.setup_completed && metrics.installation_id === null}
            name="install-metrics-settings"
            disabled={saving}
          />
          {message && (
            <p role="status" className="text-sm text-green-700">
              {message}
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm text-red-700">
              {error}
            </p>
          )}
          <Button onClick={() => void save()} disabled={!choice || choice === storedChoice(metrics) || saving}>
            Save
          </Button>
        </Card>
      )}
    </main>
  );
}
