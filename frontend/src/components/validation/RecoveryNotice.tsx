"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";

/**
 * plan_8_2 section 2.1 and owner decision 5: re-evaluate a study's checks under bioAF's current rules, on
 * request only.
 *
 * Some of the study's checks were made before bioAF bound a table to a claim's contrast, or read by an
 * earlier table decoder. Nothing is rerun until a person asks. The server's preview says what the recovery
 * reuses, fetches and re-evaluates, and that it launches no workflow; the request carries the preview's
 * fingerprint, so a study that changed in between is shown again rather than recovered unseen. The labels
 * are pending the owner's sign-off.
 */
interface RecoveryAction {
  kind: string;
  label: string;
  detail: string;
}

interface RecoveryPreview {
  available: boolean;
  launches_workflow: boolean;
  model_calls: number;
  fingerprint: string;
  actions: RecoveryAction[];
  affected_checks: { check_id: string; why: string | null }[];
  needs_approval: { check_id: string; why: string }[];
}

export function RecoveryNotice({ studyId, onChanged }: { studyId: number; onChanged: (updated: unknown) => void }) {
  const { canAccess } = usePermissions();
  const [preview, setPreview] = useState<RecoveryPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  if (!canAccess("lit_validation", "request")) return null;

  async function review() {
    setBusy(true);
    setFailed(false);
    setMessage(null);
    try {
      setPreview(await api.get<RecoveryPreview>(`/api/validation-studies/${studyId}/recovery`));
    } catch (e) {
      logError("previewing a validation study's recovery", e);
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  async function recover() {
    if (!preview) return;
    setBusy(true);
    setFailed(false);
    try {
      onChanged(
        await api.post(`/api/validation-studies/${studyId}/recovery`, { preview_fingerprint: preview.fingerprint }),
      );
      setPreview(null);
    } catch (e) {
      // A 409 is the server's plain sentence for the page: the study is busy, or changed since the preview.
      if (e instanceof Error && (e as { status?: number }).status === 409) {
        setMessage(e.message);
        setPreview(null);
      } else {
        logError("recovering a validation study", e);
        setFailed(true);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-3 rounded border border-gray-200 bg-gray-50 p-3" data-testid="recovery-notice">
      <p className="text-sm text-gray-700">
        Some of this study&apos;s checks were made under rules bioAF has since replaced. Their earlier results are
        shown as pending re-evaluation.
      </p>
      {preview ? (
        <div className="mt-2 space-y-2">
          <ul className="list-disc space-y-1 pl-5 text-sm text-gray-800">
            {preview.actions.map((action) => (
              <li key={action.kind}>
                <span className="font-medium">{action.label}</span>
                <span className="block text-xs text-gray-600">{action.detail}</span>
              </li>
            ))}
          </ul>
          {preview.needs_approval.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-gray-600">
              {preview.needs_approval.map((row) => (
                <li key={row.check_id}>Not rerun: {row.why}</li>
              ))}
            </ul>
          )}
          <p className="text-xs text-gray-600">
            No workflow is launched and no model is asked. The earlier results, classification and scorecard are kept
            in the study&apos;s history.
          </p>
          <Button busy={busy} busyLabel="Re-evaluating..." onClick={recover}>
            Re-evaluate
          </Button>
        </div>
      ) : (
        <div className="mt-2">
          <Button busy={busy} busyLabel="Loading..." variant="secondary" onClick={review}>
            Review the re-evaluation
          </Button>
        </div>
      )}
      {message && <p className="mt-2 text-sm text-gray-700">{message}</p>}
      {failed && (
        <p className="mt-2 text-sm text-red-700">
          bioAF could not complete that request. The technical detail is in the application logs.
        </p>
      )}
    </div>
  );
}
