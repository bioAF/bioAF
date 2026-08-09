"use client";

import { useEffect, useState } from "react";
import { Modal } from "@/components/shared/Modal";
import {
  ApiKey,
  ServiceAccount,
  integrationsApi,
} from "@/lib/integrationsApi";
import type { Role, RoleListResponse } from "@/lib/types";
import { api, ApiError } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { RevealSecretModal } from "./RevealSecretModal";
import { RoleEditorModal, type PermissionCatalog } from "@/components/settings/RoleEditorModal";

import { clickableRow } from "@/lib/a11y";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { useToast } from "@/components/shared/Toast";

interface Props {
  roles: Role[];
  onRolesChanged?: () => void;
}

export function ServiceAccountsTab({ roles: rolesProp, onRolesChanged }: Props) {
  const toast = useToast();
  const [roles, setRoles] = useState<Role[]>(rolesProp);
  useEffect(() => setRoles(rolesProp), [rolesProp]);
  const [accounts, setAccounts] = useState<ServiceAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Create SA
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newRoleId, setNewRoleId] = useState<number | null>(null);

  // Detail + keys
  const [selectedSa, setSelectedSa] = useState<ServiceAccount | null>(null);
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [keysLoading, setKeysLoading] = useState(false);

  // Edit SA
  const [editingSa, setEditingSa] = useState<ServiceAccount | null>(null);
  const [editName, setEditName] = useState("");
  const [editRoleId, setEditRoleId] = useState<number | null>(null);

  // Mint key
  const [showMintKey, setShowMintKey] = useState(false);
  const [newKeyName, setNewKeyName] = useState("");

  // Role catalog (for "Create custom role" shortcut)
  const [catalog, setCatalog] = useState<PermissionCatalog>({});
  const [showRoleEditor, setShowRoleEditor] = useState(false);
  // Which form should auto-select the freshly-created role
  const [pendingRoleTarget, setPendingRoleTarget] = useState<"create" | "edit" | null>(null);

  // Disable confirm
  const [pendingDisable, setPendingDisable] = useState<ServiceAccount | null>(null);

  const [revealedSecret, setRevealedSecret] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      setAccounts(await integrationsApi.listServiceAccounts());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load service accounts");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    api.get<PermissionCatalog>("/api/roles/permissions-catalog")
      .then(setCatalog)
      .catch((e) => {
        logError("loading the permission catalog", e);
        toast.error(loadFailureMessage("The permission catalog"));
      });
  }, []);

  const refreshRoles = async (): Promise<Role[]> => {
    try {
      const data = await api.get<RoleListResponse>("/api/roles");
      setRoles(data.roles);
      onRolesChanged?.();
      return data.roles;
    } catch {
      return roles;
    }
  };

  const refreshKeys = async (saId: number) => {
    setKeysLoading(true);
    try {
      setKeys(await integrationsApi.listApiKeys(saId));
    } finally {
      setKeysLoading(false);
    }
  };

  const openDetail = async (sa: ServiceAccount) => {
    setSelectedSa(sa);
    await refreshKeys(sa.id);
  };

  const openEdit = (sa: ServiceAccount) => {
    setEditingSa(sa);
    setEditName(sa.display_name ?? "");
    setEditRoleId(sa.role_id);
  };

  const handleCreateSa = async () => {
    if (!newName.trim() || !newRoleId) return;
    try {
      const created = await integrationsApi.createServiceAccount(newName.trim(), newRoleId);
      setShowCreate(false);
      setNewName("");
      setNewRoleId(null);
      await load();
      // Almost always followed by minting a key; jump straight there.
      await openDetail(created);
      setNewKeyName("");
      setShowMintKey(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create service account");
    }
  };

  const handleEditSave = async () => {
    if (!editingSa || !editName.trim() || !editRoleId) return;
    try {
      const updated = await integrationsApi.updateServiceAccount(editingSa.id, {
        display_name: editName.trim(),
        role_id: editRoleId,
      });
      setEditingSa(null);
      await load();
      if (selectedSa?.id === editingSa.id) setSelectedSa(updated);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to update service account");
    }
  };

  const handleMintKey = async () => {
    if (!selectedSa || !newKeyName.trim()) return;
    const role = roles.find((r) => r.id === selectedSa.role_id);
    const scopes = (role?.permissions ?? []).map((p) => `${p.resource}:${p.action}`);
    if (scopes.length === 0) {
      setError(
        "This service account's role grants no permissions. Edit the role or pick a different one before minting a key.",
      );
      return;
    }
    try {
      const result = await integrationsApi.mintApiKey(selectedSa.id, newKeyName.trim(), scopes);
      setRevealedSecret(result.secret);
      setShowMintKey(false);
      setNewKeyName("");
      refreshKeys(selectedSa.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to mint key");
    }
  };

  const handleRoleEditorSaved = async (saved: Role) => {
    setShowRoleEditor(false);
    const fresh = await refreshRoles();
    const match = fresh.find((r) => r.id === saved.id) ?? saved;
    if (pendingRoleTarget === "create") setNewRoleId(match.id);
    else if (pendingRoleTarget === "edit") setEditRoleId(match.id);
    setPendingRoleTarget(null);
  };

  const [pendingRevoke, setPendingRevoke] = useState<number | null>(null);

  const handleRevoke = async (keyId: number) => {
    try {
      await integrationsApi.revokeApiKey(keyId);
      if (selectedSa) refreshKeys(selectedSa.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to revoke key");
    }
  };

  const handleDisableSa = async () => {
    if (!pendingDisable) return;
    try {
      await integrationsApi.disableServiceAccount(pendingDisable.id);
      setPendingDisable(null);
      setSelectedSa(null);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to disable SA");
      setPendingDisable(null);
    }
  };

  const roleName = (roleId: number) => roles.find((r) => r.id === roleId)?.name ?? String(roleId);
  const selectedSaRole = selectedSa ? roles.find((r) => r.id === selectedSa.role_id) : null;
  const selectedSaScopeCount = selectedSaRole?.permissions.length ?? 0;

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <p className="text-sm text-gray-600">
          Service accounts authenticate external LIMS systems against the integration API.
        </p>
        <button
          onClick={() => setShowCreate(true)}
          className="px-3 py-1.5 text-sm bg-bioaf-600 text-white rounded hover:bg-bioaf-700"
        >
          Create Service Account
        </button>
      </div>
      <p className="text-xs text-gray-500 mb-4">
        Each service account holds a role that defines what its keys can do. Click a row to open
        details, mint keys, or edit.
      </p>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm">
          {error}
        </div>
      )}

      {loading ? (
        <LoadingSpinner size="lg" />
      ) : accounts.length === 0 ? (
        <div className="bg-white rounded-lg shadow p-6 text-center text-sm text-gray-500">
          No service accounts yet. Click <strong>Create Service Account</strong> above to start.
        </div>
      ) : (
        <div className="bg-white rounded-lg shadow overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Role</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {accounts.map((sa) => (
                <tr
                  key={sa.id}
                  {...clickableRow(() => openDetail(sa))}
                  className="hover:bg-gray-50 cursor-pointer"
                >
                  <td className="px-4 py-3 text-sm font-medium">{sa.display_name}</td>
                  <td className="px-4 py-3 text-sm">{roleName(sa.role_id)}</td>
                  <td className="px-4 py-3 text-sm">{sa.status}</td>
                  <td className="px-4 py-3 text-sm text-gray-500">
                    {new Date(sa.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create Service Account modal */}
      <Modal
        open={showCreate}
        title="Create Service Account"
        onClose={() => setShowCreate(false)}
        size="md"
        footer={
          <>
          <button
            onClick={() => setShowCreate(false)}
            className="px-3 py-2 text-sm bg-gray-200 rounded hover:bg-gray-300"
          >
            Cancel
          </button>
          <button
            onClick={handleCreateSa}
            disabled={!newName.trim() || !newRoleId}
            className="px-3 py-2 text-sm bg-bioaf-600 text-white rounded hover:bg-bioaf-700 disabled:opacity-50"
          >
            Create
          </button>
          </>
        }
      >
        <label htmlFor="display-name" className="block text-sm font-medium mb-1">Display name</label>
        <input id="display-name"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          className="w-full border rounded px-3 py-2 text-sm mb-3"
          placeholder="Benchling Sync"
        />
        <label htmlFor="role" className="block text-sm font-medium mb-1">Role</label>
        <div className="flex items-center gap-2 mb-2">
          <select id="role"
            value={newRoleId ?? ""}
            onChange={(e) => setNewRoleId(Number(e.target.value) || null)}
            className="flex-1 border rounded px-3 py-2 text-sm"
          >
            <option value="">Select a role</option>
            {roles.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => {
              setPendingRoleTarget("create");
              setShowRoleEditor(true);
            }}
            className="px-3 py-2 text-xs border border-bioaf-600 text-bioaf-600 rounded hover:bg-bioaf-50 whitespace-nowrap"
          >
            Create custom role
          </button>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          The role determines what every key minted under this account can do. You can change
          it later by editing the service account.
        </p>
      </Modal>

      {/* Detail modal */}
      {selectedSa && !editingSa && (
        <Modal
          open
          title={selectedSa.display_name ?? ""}
          onClose={() => setSelectedSa(null)}
          size="md"
        >
          <div className="flex justify-between items-start mb-4">
            <div>
              <p className="text-xs text-gray-500 font-mono">{selectedSa.email}</p>
              <p className="text-xs text-gray-600 mt-1">
                Role: <span className="font-medium">{roleName(selectedSa.role_id)}</span>{" "}
                <span className="text-gray-500">({selectedSaScopeCount} permissions)</span>
              </p>
            </div>
            <button
              onClick={() => setSelectedSa(null)}
              className="text-gray-500 hover:text-gray-600"
            >
              Close
            </button>
          </div>
  
          <div className="flex gap-2 mb-4">
            <button
              onClick={() => openEdit(selectedSa)}
              className="px-3 py-1.5 text-xs bg-bioaf-600 text-white rounded hover:bg-bioaf-700"
            >
              Edit
            </button>
            <button
              onClick={() => {
                setNewKeyName("");
                setShowMintKey(true);
              }}
              className="px-3 py-1.5 text-xs bg-gray-100 border rounded hover:bg-gray-200"
            >
              Mint key
            </button>
            <button
              onClick={() => setPendingDisable(selectedSa)}
              className="px-3 py-1.5 text-xs bg-red-600 text-white rounded hover:bg-red-700 ml-auto"
            >
              Disable Service Account
            </button>
          </div>
  
          <div>
            <h3 className="text-sm font-semibold mb-2">API Keys</h3>
            {keysLoading ? (
              <LoadingSpinner size="sm" />
            ) : keys.length === 0 ? (
              <p className="text-sm text-gray-500">No keys yet. Click <strong>Mint key</strong> above.</p>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th scope="col" className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                    <th scope="col" className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase">Prefix</th>
                    <th scope="col" className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th scope="col" className="px-2 py-2"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {keys.map((k) => (
                    <tr key={k.id}>
                      <td className="px-2 py-2">{k.name}</td>
                      <td className="px-2 py-2 font-mono text-xs">biokey_{k.key_prefix}</td>
                      <td className="px-2 py-2">{k.revoked_at ? "revoked" : "active"}</td>
                      <td className="px-2 py-2 text-right">
                        {!k.revoked_at && (
                          <button
                            onClick={() => setPendingRevoke(k.id)}
                            className="text-xs text-red-600 hover:underline"
                          >
                            Revoke
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Modal>
      )}

      {/* Edit Service Account modal */}
      {editingSa && (
        <Modal
          open
          title={`Edit ${editingSa.display_name}`}
          onClose={() => setEditingSa(null)}
          size="md"
          footer={
            <>
            <button
              onClick={() => setEditingSa(null)}
              className="px-4 py-2 text-sm text-gray-700 bg-gray-100 rounded hover:bg-gray-200"
            >
              Cancel
            </button>
            <button
              onClick={handleEditSave}
              disabled={!editName.trim() || !editRoleId}
              className="px-4 py-2 text-sm text-white bg-bioaf-600 rounded hover:bg-bioaf-700 disabled:opacity-50"
            >
              Save Changes
            </button>
            </>
          }
        >
          <div className="space-y-4">
            <div>
              <label htmlFor="display-name-2" className="block text-sm font-medium text-gray-700 mb-1">Display name</label>
              <input id="display-name-2"
                type="text"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-bioaf-500"
              />
            </div>
            <div>
              <label htmlFor="role-2" className="block text-sm font-medium text-gray-700 mb-1">Role</label>
              <div className="flex items-center gap-2">
                <select id="role-2"
                  value={editRoleId ?? ""}
                  onChange={(e) => setEditRoleId(Number(e.target.value) || null)}
                  className="flex-1 px-3 py-2 border rounded-md text-sm"
                >
                  {roles.map((r) => (
                    <option key={r.id} value={r.id}>{r.name}</option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => {
                    setPendingRoleTarget("edit");
                    setShowRoleEditor(true);
                  }}
                  className="px-3 py-2 text-xs border border-bioaf-600 text-bioaf-600 rounded hover:bg-bioaf-50 whitespace-nowrap"
                >
                  Create custom role
                </button>
              </div>
            </div>
          </div>
        </Modal>
      )}

      {/* Mint API Key modal (scopes derived from the SA's role) */}
      {showMintKey && selectedSa && (
        <Modal
          open
          title="Mint API Key"
          onClose={() => setShowMintKey(false)}
          size="md"
          footer={
            <>
            <button
              onClick={() => setShowMintKey(false)}
              className="px-3 py-2 text-sm bg-gray-200 rounded hover:bg-gray-300"
            >
              Cancel
            </button>
            <button
              onClick={handleMintKey}
              disabled={!newKeyName.trim() || selectedSaScopeCount === 0}
              className="px-3 py-2 text-sm bg-bioaf-600 text-white rounded hover:bg-bioaf-700 disabled:opacity-50"
            >
              Mint
            </button>
            </>
          }
        >
          <p className="text-xs text-gray-500 mb-4">
            Permissions are inherited from the service account&apos;s role:{" "}
            <span className="font-medium">{roleName(selectedSa.role_id)}</span>{" "}
            ({selectedSaScopeCount} permissions).
            To change what the key can do, edit the service account&apos;s role.
          </p>
          <label htmlFor="key-name" className="block text-sm font-medium mb-1">Key name</label>
          <input id="key-name"
            value={newKeyName}
            onChange={(e) => setNewKeyName(e.target.value)}
            className="w-full border rounded px-3 py-2 text-sm mb-4"
            placeholder="Primary"
          />
        </Modal>
      )}

      {showRoleEditor && (
        <RoleEditorModal
          editingRole={null}
          catalog={catalog}
          onClose={() => {
            setShowRoleEditor(false);
            setPendingRoleTarget(null);
          }}
          onSaved={handleRoleEditorSaved}
        />
      )}

      {pendingDisable && (
        <ConfirmDialog
          open={true}
          title="Disable Service Account"
          message={`Disable ${pendingDisable.display_name}? All of its API keys will be revoked. This cannot be undone.`}
          confirmLabel="Disable"
          onConfirm={handleDisableSa}
          onCancel={() => setPendingDisable(null)}
        />
      )}

      <ConfirmDialog
        open={pendingRevoke !== null}
        variant="danger"
        title="Revoke this API key?"
        message={
          <>
            <p>
              Any automation still using this key stops working immediately, and the key
              cannot be restored.
            </p>
            <p>Issue a new key first if something is depending on this one.</p>
          </>
        }
        confirmLabel="Revoke key"
        onConfirm={() => {
          const id = pendingRevoke;
          setPendingRevoke(null);
          if (id !== null) handleRevoke(id);
        }}
        onCancel={() => setPendingRevoke(null)}
      />

      {revealedSecret && (
        <RevealSecretModal
          title="API key secret"
          secret={revealedSecret}
          onClose={() => setRevealedSecret(null)}
        />
      )}
    </div>
  );
}
