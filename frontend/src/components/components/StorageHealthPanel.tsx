"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { components } from "../../../contracts/internal-api";
import { useInfrastructureActions } from "@/hooks/useInfrastructureActions";
import { logError } from "@/lib/errorReporting";
import { Alert } from "@/components/shared/Alert";
import { Modal } from "@/components/shared/Modal";
import { Button } from "@/components/ui/Button";

type StorageHealth = components["schemas"]["StorageHealthResponse"];
type StoreHealthItem = components["schemas"]["StoreHealthItem"];
type Health = StoreHealthItem["health"];

const HEALTH_PATH = "/api/v1/infrastructure/storage/health";

/** Chip wording signed off in mockup M14. */
export const HEALTH_LABELS: Record<Health, string> = {
  available: "Available",
  not_found: "Not found",
  no_access: "No access",
  not_responding: "Not responding",
  not_confirmed: "Not confirmed",
  wrong_owner: "Belongs to another installation",
  not_mounted: "Not mounted",
  cannot_check: "Cannot check",
};

const HEALTH_TONES: Record<Health, string> = {
  available: "bg-green-100 text-green-800",
  not_found: "bg-red-100 text-red-800",
  no_access: "bg-red-100 text-red-800",
  not_responding: "bg-amber-100 text-amber-800",
  not_confirmed: "bg-amber-100 text-amber-800",
  wrong_owner: "bg-red-100 text-red-800",
  not_mounted: "bg-red-100 text-red-800",
  cannot_check: "bg-gray-100 text-gray-700",
};

function checkedAgo(checkedAt: string | null | undefined): string | null {
  if (!checkedAt) return null;
  const minutes = Math.max(0, Math.round((Date.now() - new Date(checkedAt).getTime()) / 60_000));
  if (minutes < 1) return "Checked just now";
  if (minutes === 1) return "Checked 1 minute ago";
  if (minutes < 60) return `Checked ${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  return hours === 1 ? "Checked 1 hour ago" : `Checked ${hours} hours ago`;
}

function observedLine(item: StoreHealthItem): string | null {
  const parts: string[] = [];
  if (item.observed_owner) parts.push(`project ${item.observed_owner}`);
  if (item.observed_created_at) parts.push(`created ${item.observed_created_at.slice(0, 10)}`);
  return parts.length ? parts.join(" · ") : null;
}

function isHealth(data: unknown): data is StorageHealth {
  return Boolean(data && Array.isArray((data as StorageHealth).stores));
}

function ConfirmBucketsDialog({
  buckets,
  onClose,
  onConfirmed,
}: {
  buckets: StoreHealthItem[];
  onClose: () => void;
  onConfirmed: (health: StorageHealth) => void;
}) {
  const [reason, setReason] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<StorageHealth>("/api/v1/infrastructure/storage/identity/confirm", {
        stores: buckets.map((b) => b.store),
        reason: reason.trim(),
        confirm: true,
      });
      onConfirmed(result);
    } catch (e) {
      logError("confirming storage buckets", e);
      setError(e instanceof ApiError ? e.message : "The buckets could not be confirmed. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      title="Confirm storage buckets"
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || !acknowledged || !reason.trim()}>
            Confirm buckets
          </Button>
        </>
      }
    >
      <ul className="mb-4 space-y-2">
        {buckets.map((b) => (
          <li key={b.store} className="text-sm">
            <span className="font-mono text-gray-800">{b.bucket_name}</span>
            {observedLine(b) && <span className="block text-xs text-gray-500">{observedLine(b)}</span>}
          </li>
        ))}
      </ul>
      <label className="block text-sm font-medium text-gray-700" htmlFor="confirm-buckets-reason">
        Reason (saved in the audit log)
      </label>
      <textarea
        id="confirm-buckets-reason"
        className="mt-1 w-full rounded border border-gray-300 p-2 text-sm"
        rows={3}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <label className="mt-3 flex items-start gap-2 text-sm text-gray-700">
        <input
          type="checkbox"
          className="mt-1"
          checked={acknowledged}
          onChange={(e) => setAcknowledged(e.target.checked)}
        />
        <span>
          I confirm these buckets belong to this bioAF installation. This records them as of today; it does not
          prove their earlier history.
        </span>
      </label>
      {error && (
        <Alert tone="error" className="mt-3">
          {error}
        </Alert>
      )}
    </Modal>
  );
}

/**
 * Read-only health of each storage bucket (mockup M14) and confirmation of buckets bioAF could
 * not establish on its own (mockup M15). Checking never creates or changes anything in the cloud.
 */
export function StorageHealthPanel() {
  const actions = useInfrastructureActions();
  const [health, setHealth] = useState<StorageHealth | null>(null);
  const [checking, setChecking] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const isAdmin = actions.allowed("deploy") || actions.allowed("configure");
  const canConfirm = actions.allowed("configure");

  useEffect(() => {
    api
      .get<StorageHealth>(HEALTH_PATH)
      .then((data) => {
        if (isHealth(data)) setHealth(data);
      })
      .catch((e) => logError("loading storage health", e));
  }, []);

  const checkAgain = async () => {
    setChecking(true);
    try {
      const data = await api.post<StorageHealth>(`${HEALTH_PATH}/check`, {});
      if (isHealth(data)) setHealth(data);
    } catch (e) {
      logError("checking storage health", e);
    } finally {
      setChecking(false);
    }
  };

  if (!health || health.stores.length === 0) return null;

  const missing = health.stores.filter((s) => s.health === "not_found").length;
  const foreign = health.stores.some((s) => s.health === "wrong_owner");
  const unconfirmed = health.stores.filter((s) => s.health === "not_confirmed");

  return (
    <section className="space-y-3" aria-label="Storage health">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-800">Storage</h3>
        <div className="flex items-center gap-3 text-xs text-gray-500">
          {checkedAgo(health.checked_at) && <span>{checkedAgo(health.checked_at)}</span>}
          {isAdmin && (
            <Button variant="secondary" size="sm" onClick={checkAgain} disabled={checking}>
              Check again
            </Button>
          )}
        </div>
      </div>
      <ul className="divide-y divide-gray-100 rounded border border-gray-200 bg-white">
        {health.stores.map((s) => (
          <li
            key={s.store}
            data-testid={`store-health-${s.bucket_name}`}
            className="flex items-center justify-between px-3 py-2"
          >
            <span className="font-mono text-sm text-gray-800">{s.bucket_name}</span>
            <span
              data-testid="health-chip"
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${HEALTH_TONES[s.health]}`}
            >
              {HEALTH_LABELS[s.health]}
            </span>
          </li>
        ))}
      </ul>
      {missing > 0 && (
        <Alert tone="error">
          {missing === 1 ? "1 bucket was not found." : `${missing} buckets were not found.`} Files stored there
          cannot be read and new results cannot be saved. To create it again, use Check for Infrastructure Updates.
        </Alert>
      )}
      {foreign && (
        <Alert tone="error">
          A bucket with this name exists but belongs to another installation. bioAF will not use, empty, or delete
          it.
        </Alert>
      )}
      {canConfirm && unconfirmed.length > 0 && (
        <Alert
          tone="warning"
          action={
            <Button size="sm" onClick={() => setConfirming(true)}>
              Review and confirm buckets
            </Button>
          }
        >
          bioAF could not confirm that{" "}
          {unconfirmed.length === 1 ? "1 storage bucket is the one" : `${unconfirmed.length} storage buckets are the ones`}{" "}
          it created. Uploads, downloads, and pipelines keep working. Destroy Storage and automatic repairs are
          unavailable for these buckets until an administrator confirms them.
        </Alert>
      )}
      {confirming && (
        <ConfirmBucketsDialog
          buckets={unconfirmed}
          onClose={() => setConfirming(false)}
          onConfirmed={(data) => {
            if (isHealth(data)) setHealth(data);
            setConfirming(false);
          }}
        />
      )}
    </section>
  );
}
