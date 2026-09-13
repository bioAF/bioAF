"use client";

import { useId, useState } from "react";

import { Button } from "@/components/ui/Button";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";
import { logError } from "@/lib/errorReporting";

/**
 * plan_8_1 section 2.1: bioAF could not group the paper's claims into findings, and the claims stand.
 *
 * Grouping them again runs a new inventory over the SAME committed claims, from the text the read
 * recorded. When that text was pasted, bioAF did not keep it: the server answers 409 with a plain
 * sentence asking for it, and this asks the person to paste it again. The server decides everything
 * else (a changed text is read again, never regrouped).
 */
export function InventoryRetryNotice({ studyId, onChanged }: { studyId: number; onChanged: (updated: unknown) => void }) {
  const { canAccess } = usePermissions();
  const [busy, setBusy] = useState(false);
  const [askedForText, setAskedForText] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [failed, setFailed] = useState(false);
  const textId = useId();

  if (!canAccess("lit_validation", "request")) return null;

  async function retry(fullText?: string) {
    setBusy(true);
    setFailed(false);
    try {
      onChanged(
        await api.post(`/api/validation-studies/${studyId}/inventory/retry`, fullText ? { full_text: fullText } : {}),
      );
      setAskedForText(null);
    } catch (e) {
      // A 409 is the server asking for the paper's text; its message is a plain sentence meant for the page.
      if (e instanceof Error && (e as { status?: number }).status === 409) {
        setAskedForText(e.message);
      } else {
        logError("grouping the paper's claims into findings again", e);
        setFailed(true);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-3 rounded border border-gray-200 bg-gray-50 p-3" data-testid="inventory-retry">
      {askedForText ? (
        <div className="space-y-2">
          <p className="text-sm text-gray-700">{askedForText}</p>
          <label htmlFor={textId} className="block text-xs font-medium text-gray-700">
            The paper&apos;s text
          </label>
          <textarea
            id={textId}
            value={text}
            onChange={(event) => setText(event.target.value)}
            rows={6}
            className="w-full rounded border border-gray-300 p-2 text-sm"
          />
          <Button busy={busy} busyLabel="Grouping..." disabled={!text.trim()} onClick={() => retry(text)}>
            Group the claims with this text
          </Button>
        </div>
      ) : (
        <Button busy={busy} busyLabel="Grouping..." onClick={() => retry()}>
          Group the claims into findings again
        </Button>
      )}
      {failed && (
        <p className="mt-2 text-sm text-red-700">
          bioAF could not group the claims again. The technical detail is in the application logs.
        </p>
      )}
    </div>
  );
}
