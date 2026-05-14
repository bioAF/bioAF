"use client";

import { useEffect, useState } from "react";
import {
  WebhookDelivery,
  WebhookSubscription,
  integrationsApi,
} from "@/lib/integrationsApi";
import { ApiError } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { RevealSecretModal } from "./RevealSecretModal";

const VALID_EVENTS = [
  "experiment.created",
  "experiment.updated",
  "experiment.status_changed",
  "sample.created",
  "sample.updated",
  "sample.qc_changed",
  "file.registered",
  "file.ready",
];

export function WebhooksTab() {
  const [subs, setSubs] = useState<WebhookSubscription[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Create
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const [newEvents, setNewEvents] = useState<Set<string>>(new Set());

  // Detail
  const [selectedSub, setSelectedSub] = useState<WebhookSubscription | null>(null);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>("");

  // Edit
  const [editingSub, setEditingSub] = useState<WebhookSubscription | null>(null);
  const [editName, setEditName] = useState("");
  const [editUrl, setEditUrl] = useState("");
  const [editEvents, setEditEvents] = useState<Set<string>>(new Set());
  const [editActive, setEditActive] = useState(true);

  // Disable confirm
  const [pendingDisable, setPendingDisable] = useState<WebhookSubscription | null>(null);

  const [revealedSecret, setRevealedSecret] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      setSubs(await integrationsApi.listWebhooks());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load webhooks");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, []);

  const refreshDeliveries = async (subId: number) => {
    try {
      setDeliveries(
        await integrationsApi.listWebhookDeliveries(subId, {
          status: statusFilter || undefined,
          limit: 50,
        }),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load deliveries");
    }
  };

  useEffect(() => {
    if (selectedSub) refreshDeliveries(selectedSub.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSub, statusFilter]);

  const handleCreate = async () => {
    if (!newName.trim() || !newUrl.trim() || newEvents.size === 0) return;
    try {
      const res = await integrationsApi.createWebhook({
        name: newName.trim(),
        url: newUrl.trim(),
        events: Array.from(newEvents),
      });
      setRevealedSecret(res.secret);
      setShowCreate(false);
      setNewName("");
      setNewUrl("");
      setNewEvents(new Set());
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create webhook");
    }
  };

  const openEdit = (s: WebhookSubscription) => {
    setEditingSub(s);
    setEditName(s.name);
    setEditUrl(s.url);
    setEditEvents(new Set(s.events));
    setEditActive(s.is_active);
  };

  const handleEditSave = async () => {
    if (!editingSub || !editName.trim() || !editUrl.trim() || editEvents.size === 0) return;
    try {
      const updated = await integrationsApi.updateWebhook(editingSub.id, {
        name: editName.trim(),
        url: editUrl.trim(),
        events: Array.from(editEvents),
        is_active: editActive,
      });
      setEditingSub(null);
      await load();
      if (selectedSub?.id === editingSub.id) setSelectedSub(updated);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to update webhook");
    }
  };

  const handleTest = async (subId: number) => {
    try {
      await integrationsApi.fireTestWebhook(subId);
      if (selectedSub) refreshDeliveries(selectedSub.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to send test event");
    }
  };

  const handleReplay = async (deliveryId: number) => {
    try {
      await integrationsApi.replayWebhookDelivery(deliveryId);
      if (selectedSub) refreshDeliveries(selectedSub.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to replay");
    }
  };

  const handleRotate = async (subId: number) => {
    try {
      const res = await integrationsApi.rotateWebhookSecret(subId);
      setRevealedSecret(res.secret);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to rotate secret");
    }
  };

  const handleDisable = async () => {
    if (!pendingDisable) return;
    try {
      await integrationsApi.disableWebhook(pendingDisable.id);
      setPendingDisable(null);
      setSelectedSub(null);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to disable webhook");
      setPendingDisable(null);
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <p className="text-sm text-gray-600">
          Webhook subscriptions notify external systems when bioAF entities change.
        </p>
        <button
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 text-sm bg-bioaf-600 text-white rounded hover:bg-bioaf-700"
        >
          Create Webhook
        </button>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm">
          {error}
        </div>
      )}

      {loading ? (
        <LoadingSpinner size="lg" />
      ) : subs.length === 0 ? (
        <div className="bg-white rounded-lg shadow p-6 text-center text-sm text-gray-500">
          No webhooks yet. Create one to forward bioAF events to an external system.
        </div>
      ) : (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">URL</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Events</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {subs.map((s) => (
                <tr
                  key={s.id}
                  onClick={() => setSelectedSub(s)}
                  className="hover:bg-gray-50 cursor-pointer"
                >
                  <td className="px-4 py-3 text-sm font-medium">{s.name}</td>
                  <td className="px-4 py-3 text-sm text-gray-500 truncate max-w-xs">{s.url}</td>
                  <td className="px-4 py-3 text-sm">{s.events.length}</td>
                  <td className="px-4 py-3 text-sm">{s.is_active ? "active" : "disabled"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create Webhook modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-40 p-4">
          <div className="bg-white rounded-lg shadow-xl p-6 max-w-lg w-full">
            <h2 className="text-lg font-semibold mb-4">Create Webhook</h2>
            <label className="block text-sm font-medium mb-1">Name</label>
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm mb-3"
              placeholder="LIMS bridge"
            />
            <label className="block text-sm font-medium mb-1">URL</label>
            <input
              value={newUrl}
              onChange={(e) => setNewUrl(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm mb-3"
              placeholder="https://lims.example.com/hooks/bioaf"
            />
            <label className="block text-sm font-medium mb-2">Events</label>
            <div className="grid grid-cols-2 gap-2 mb-4">
              {VALID_EVENTS.map((e) => (
                <label key={e} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={newEvents.has(e)}
                    onChange={(ev) => {
                      const next = new Set(newEvents);
                      if (ev.target.checked) next.add(e);
                      else next.delete(e);
                      setNewEvents(next);
                    }}
                  />
                  <span>{e}</span>
                </label>
              ))}
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowCreate(false)}
                className="px-3 py-2 text-sm bg-gray-200 rounded hover:bg-gray-300"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={!newName.trim() || !newUrl.trim() || newEvents.size === 0}
                className="px-3 py-2 text-sm bg-bioaf-600 text-white rounded hover:bg-bioaf-700 disabled:opacity-50"
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Detail modal */}
      {selectedSub && !editingSub && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-30 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-3xl w-full max-h-[90vh] overflow-y-auto p-6">
            <div className="flex justify-between items-start mb-4">
              <div>
                <h2 className="text-lg font-semibold">{selectedSub.name}</h2>
                <p className="text-xs text-gray-500 break-all">{selectedSub.url}</p>
                <p className="text-xs text-gray-600 mt-1">
                  {selectedSub.events.length} event{selectedSub.events.length === 1 ? "" : "s"}{" "}
                  &middot; {selectedSub.is_active ? "active" : "disabled"}
                </p>
              </div>
              <button
                onClick={() => setSelectedSub(null)}
                className="text-gray-400 hover:text-gray-600"
              >
                Close
              </button>
            </div>

            <div className="flex flex-wrap gap-2 mb-4">
              <button
                onClick={() => openEdit(selectedSub)}
                className="px-3 py-1.5 text-xs bg-bioaf-600 text-white rounded hover:bg-bioaf-700"
              >
                Edit
              </button>
              <button
                onClick={() => handleTest(selectedSub.id)}
                className="px-3 py-1.5 text-xs bg-gray-100 border rounded hover:bg-gray-200"
              >
                Send test event
              </button>
              <button
                onClick={() => handleRotate(selectedSub.id)}
                className="px-3 py-1.5 text-xs bg-gray-100 border rounded hover:bg-gray-200"
              >
                Rotate secret
              </button>
              <button
                onClick={() => setPendingDisable(selectedSub)}
                className="px-3 py-1.5 text-xs bg-red-600 text-white rounded hover:bg-red-700 ml-auto"
              >
                Disable Webhook
              </button>
            </div>

            <div className="mb-3 flex items-center gap-2">
              <label className="text-xs text-gray-500">Status filter:</label>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="text-xs border rounded px-2 py-1"
              >
                <option value="">All</option>
                <option value="pending">pending</option>
                <option value="delivered">delivered</option>
                <option value="failed">failed</option>
                <option value="dead_letter">dead_letter</option>
              </select>
            </div>

            <table className="w-full text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase">Event</th>
                  <th className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                  <th className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase">Attempts</th>
                  <th className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase">Last Status</th>
                  <th className="px-2 py-2"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {deliveries.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-2 py-4 text-xs text-gray-500 text-center">
                      No deliveries yet.
                    </td>
                  </tr>
                ) : (
                  deliveries.map((d) => (
                    <tr key={d.id}>
                      <td className="px-2 py-2 text-xs">{d.event_type}</td>
                      <td className="px-2 py-2 text-xs">{d.status}</td>
                      <td className="px-2 py-2 text-xs">{d.attempt_count}</td>
                      <td className="px-2 py-2 text-xs">{d.last_response_status ?? "-"}</td>
                      <td className="px-2 py-2 text-xs">
                        <button
                          onClick={() => handleReplay(d.id)}
                          className="text-xs text-bioaf-600 hover:underline"
                        >
                          Replay
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Edit Webhook modal */}
      {editingSub && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-lg w-full p-6">
            <h3 className="text-lg font-semibold mb-4">Edit {editingSub.name}</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                <input
                  type="text"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-bioaf-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">URL</label>
                <input
                  type="text"
                  value={editUrl}
                  onChange={(e) => setEditUrl(e.target.value)}
                  className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-bioaf-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Events</label>
                <div className="grid grid-cols-2 gap-2">
                  {VALID_EVENTS.map((e) => (
                    <label key={e} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={editEvents.has(e)}
                        onChange={(ev) => {
                          const next = new Set(editEvents);
                          if (ev.target.checked) next.add(e);
                          else next.delete(e);
                          setEditEvents(next);
                        }}
                      />
                      <span>{e}</span>
                    </label>
                  ))}
                </div>
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={editActive}
                  onChange={(e) => setEditActive(e.target.checked)}
                />
                <span>Active (deliveries will be queued for this subscription)</span>
              </label>
            </div>
            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => setEditingSub(null)}
                className="px-4 py-2 text-sm text-gray-700 bg-gray-100 rounded hover:bg-gray-200"
              >
                Cancel
              </button>
              <button
                onClick={handleEditSave}
                disabled={!editName.trim() || !editUrl.trim() || editEvents.size === 0}
                className="px-4 py-2 text-sm text-white bg-bioaf-600 rounded hover:bg-bioaf-700 disabled:opacity-50"
              >
                Save Changes
              </button>
            </div>
          </div>
        </div>
      )}

      {pendingDisable && (
        <ConfirmDialog
          open={true}
          title="Disable Webhook"
          message={`Disable ${pendingDisable.name}? No further deliveries will be queued.`}
          confirmLabel="Disable"
          onConfirm={handleDisable}
          onCancel={() => setPendingDisable(null)}
        />
      )}

      {revealedSecret && (
        <RevealSecretModal
          title="Webhook secret"
          secret={revealedSecret}
          description="This is the only time we will show this. Use it to verify the X-bioAF-Signature header on your receiver."
          onClose={() => setRevealedSecret(null)}
        />
      )}
    </div>
  );
}
