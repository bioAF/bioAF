"use client";

import { useEffect, useState } from "react";
import { Modal } from "@/components/shared/Modal";
import { Button } from "@/components/ui/Button";
import { usePermissions } from "@/hooks/usePermissions";
import { logError } from "@/lib/errorReporting";
import {
  fetchSystemSettings,
  isChoiceConflict,
  saveInstallMetricsChoice,
  type InstallMetricsChoice,
  type InstallMetricsSettings,
} from "@/lib/installMetrics";
import { InstallMetricsDisclosure } from "./InstallMetricsDisclosure";

/**
 * Asks an admin who can configure the installation for the installation metrics choice while it is still
 * pending (ADR-075): installations set up before metrics existed, and setups that never recorded one.
 * Closing leaves it pending for a later session; either saved choice resolves it for every admin. A failed
 * read shows nothing and never blocks the app.
 */
export function InstallMetricsPrompt() {
  const { canAccess, loading } = usePermissions();
  const allowed = !loading && Boolean(canAccess?.("infrastructure", "configure"));
  const [current, setCurrent] = useState<InstallMetricsSettings | null>(null);
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<InstallMetricsChoice | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!allowed) return;
    const controller = new AbortController();
    fetchSystemSettings(controller.signal)
      .then((settings) => {
        const metrics = settings?.install_metrics;
        if (metrics?.prompt_required) {
          setCurrent(metrics);
          setOpen(true);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) logError("reading the installation metrics setting", e);
      });
    return () => controller.abort();
  }, [allowed]);

  if (!open || !current) return null;

  const save = async () => {
    if (!choice) return;
    setSaving(true);
    setError("");
    try {
      await saveInstallMetricsChoice(current, choice);
      setOpen(false);
    } catch (e) {
      if (isChoiceConflict(e)) {
        try {
          const latest = (await fetchSystemSettings())?.install_metrics;
          if (latest && !latest.prompt_required) {
            setOpen(false);
            return;
          }
          if (latest) setCurrent(latest);
        } catch (readError) {
          logError("reading the installation metrics setting", readError);
        }
      }
      logError("saving the installation metrics choice", e);
      setError("Your choice was not saved. Try again.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      title="Installation metrics"
      onClose={() => setOpen(false)}
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={() => setOpen(false)}>
            Decide later
          </Button>
          <Button onClick={() => void save()} disabled={!choice || saving}>
            Save choice
          </Button>
        </>
      }
    >
      <InstallMetricsDisclosure
        value={choice}
        onChange={setChoice}
        alreadyCompleted={current.setup_completed}
        name="install-metrics-prompt"
      />
      {error && (
        <p role="alert" className="mt-3 text-sm text-red-700">
          {error}
        </p>
      )}
    </Modal>
  );
}
