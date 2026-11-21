"use client";

import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import { useResource } from "@/hooks/useResource";
import { usePermissions } from "@/hooks/usePermissions";
import { useInfrastructureActions } from "@/hooks/useInfrastructureActions";
import { useInfrastructureOperations } from "@/hooks/useInfrastructureOperations";
import { logError } from "@/lib/errorReporting";
import { DEFINITION_NOTICE_PATH, displayVersion, isDefinitionNotice, type DefinitionNotice } from "@/lib/definitionNotice";
import { infrastructure } from "@/lib/infrastructure";
import { Modal } from "@/components/shared/Modal";
import { Button } from "@/components/ui/Button";

function newKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Infrastructure changes after a bioAF update (mockup M17): shown once to each admin with
 * infrastructure deploy, per notice. "Later" records only this admin's acknowledgment and never
 * marks anything applied; "Apply now" (or "Check now") starts the same confirmed update as M1.
 */
export function InfrastructureChangesPopup() {
  const { canAccess, loading, failed } = usePermissions();
  const canDeploy = !loading && !failed && canAccess("infrastructure", "deploy");
  if (!canDeploy) return null;
  return <PopupForDeployer />;
}

function PopupForDeployer() {
  const read = useResource<DefinitionNotice>(
    "infrastructure:definition-notice",
    useCallback((signal: AbortSignal) => api.get<DefinitionNotice>(DEFINITION_NOTICE_PATH, { signal }), []),
    { onError: (e) => logError("reading pending infrastructure changes", e) },
  );
  const actions = useInfrastructureActions();
  const operations = useInfrastructureOperations();
  const [closed, setClosed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const data = isDefinitionNotice(read.data) ? read.data : null;
  if (closed || !data?.pending || !data.notice || data.seen) return null;
  const notice = data.notice;
  const version = displayVersion(notice.version);
  const unknown = notice.unknown_baseline;
  const applyGate = actions.gate("deploy");

  const acknowledge = async () => {
    try {
      await api.post(`${DEFINITION_NOTICE_PATH}/${notice.id}/seen`, {});
    } catch (e) {
      logError("recording that the infrastructure notice was seen", e);
    }
  };

  const later = async () => {
    setClosed(true);
    await acknowledge();
  };

  const apply = async () => {
    if (!actions.stillAllowed("deploy")) return;
    setBusy(true);
    setError(null);
    try {
      await acknowledge();
      const accepted = await infrastructure.checkUpdates(newKey());
      setClosed(true);
      operations?.refresh();
      operations?.open(accepted.operation_id);
    } catch (e) {
      if (operations?.showConflict(e, "infrastructure update")) {
        setClosed(true);
        return;
      }
      setError(e instanceof Error ? e.message : "The update could not be started.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      title={unknown ? "Check your infrastructure for this version" : "Infrastructure changes are ready to apply"}
      onClose={() => void later()}
      footer={
        <>
          <Button variant="secondary" onClick={() => void later()} disabled={busy}>
            Later
          </Button>
          <Button onClick={() => void apply()} disabled={busy || Boolean(applyGate.disabled)} title={applyGate.title}>
            {unknown ? "Check now" : "Apply now"}
          </Button>
        </>
      }
    >
      {!unknown && <p className="mb-2 text-xs font-medium text-gray-500">{version}</p>}
      <div className="space-y-2 text-sm text-gray-700">
        {unknown ? (
          <p>
            bioAF can&apos;t tell which infrastructure changes were applied before this update. Run a check to add
            anything this version needs. Existing resources and data are never deleted or replaced by this step.
          </p>
        ) : (
          <>
            <p>
              bioAF was updated to {version}. This version includes changes to your cloud infrastructure, such as new
              storage. Some new features need them.
            </p>
            <p>Applying adds or updates resources. Existing resources and data are never deleted or replaced by this step.</p>
            <p>
              You can also do this later from Infrastructure &gt; Components &gt; Infrastructure updates. While it runs,
              other infrastructure actions are unavailable to every administrator.
            </p>
          </>
        )}
        {error && <p className="text-red-700">{error}</p>}
      </div>
    </Modal>
  );
}
