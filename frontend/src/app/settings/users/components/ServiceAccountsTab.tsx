"use client";

import { useEffect, useState } from "react";
import {
  ApiKey,
  ServiceAccount,
  integrationsApi,
} from "@/lib/integrationsApi";
import type { Role } from "@/lib/types";
import { ApiError } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { RevealSecretModal } from "./RevealSecretModal";

interface Props {
  roles: Role[];
}

export function ServiceAccountsTab({ roles }: Props) {
  const [accounts, setAccounts] = useState<ServiceAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newRoleId, setNewRoleId] = useState<number | null>(null);

  const [selectedSa, setSelectedSa] = useState<ServiceAccount | null>(null);
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [keysLoading, setKeysLoading] = useState(false);

  const [scopeAlphabet, setScopeAlphabet] = useState<string[]>([]);
  const [showMintKey, setShowMintKey] = useState(false);
  const [newKeyName, setNewKeyName] = useState("");
  const [newKeyScopes, setNewKeyScopes] = useState<Set<string>>(new Set());

  const [revealedSecret, setRevealedSecret] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const sas = await integrationsApi.listServiceAccounts();
      setAccounts(sas);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load service accounts");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    integrationsApi
      .listScopeAlphabet()
      .then((d) => setScopeAlphabet(d.scopes))
      .catch(() => {});
  }, []);

  const refreshKeys = async (saId: number) => {
    setKeysLoading(true);
    try {
      const k = await integrationsApi.listApiKeys(saId);
      setKeys(k);
    } finally {
      setKeysLoading(false);
    }
  };

  const handleCreateSa = async () => {
    if (!newName.trim() || !newRoleId) return;
    try {
      const created = await integrationsApi.createServiceAccount(newName.trim(), newRoleId);
      setShowCreate(false);
      setNewName("");
      setNewRoleId(null);
      await load();
      // Open the new SA's drawer and jump straight to the mint-key modal.
      // Creating a service account is almost always immediately followed by
      // minting a key for it; making that two clicks hides the path. Open
      // the drawer auto-expanded so "Mint key" is visible without hunting.
      setSelectedSa(created);
      await refreshKeys(created.id);
      setShowMintKey(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create service account");
    }
  };

  const handleMintKey = async () => {
    if (!selectedSa || !newKeyName.trim() || newKeyScopes.size === 0) return;
    try {
      const result = await integrationsApi.mintApiKey(
        selectedSa.id,
        newKeyName.trim(),
        Array.from(newKeyScopes),
      );
      setRevealedSecret(result.secret);
      setShowMintKey(false);
      setNewKeyName("");
      setNewKeyScopes(new Set());
      refreshKeys(selectedSa.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to mint key");
    }
  };

  const handleRevoke = async (keyId: number) => {
    try {
      await integrationsApi.revokeApiKey(keyId);
      if (selectedSa) refreshKeys(selectedSa.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to revoke key");
    }
  };

  const handleDisableSa = async (sa: ServiceAccount) => {
    if (!window.confirm(`Disable ${sa.display_name}? All its keys will be revoked.`)) {
      return;
    }
    try {
      await integrationsApi.disableServiceAccount(sa.id);
      setSelectedSa(null);
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to disable SA");
    }
  };

  const openDetail = (sa: ServiceAccount) => {
    setSelectedSa(sa);
    refreshKeys(sa.id);
  };

  const groupedScopes = scopeAlphabet.reduce<Record<string, string[]>>((acc, s) => {
    const [resource] = s.split(":");
    acc[resource] = acc[resource] || [];
    acc[resource].push(s);
    return acc;
  }, {});

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
        API keys live under each service account. Create an account, then click its row to mint a
        scoped key. The full <code className="bg-gray-100 px-1 rounded">biokey_…</code> secret is
        shown exactly once at mint time.
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
          Once created, you can click into it to mint an API key.
        </div>
      ) : (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Role</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {accounts.map((sa) => {
                const role = roles.find((r) => r.id === sa.role_id);
                return (
                  <tr
                    key={sa.id}
                    onClick={() => openDetail(sa)}
                    className="hover:bg-gray-50 cursor-pointer"
                  >
                    <td className="px-4 py-3 text-sm font-medium">{sa.display_name}</td>
                    <td className="px-4 py-3 text-sm">{role?.name ?? sa.role_id}</td>
                    <td className="px-4 py-3 text-sm">{sa.status}</td>
                    <td className="px-4 py-3 text-sm text-gray-500">
                      {new Date(sa.created_at).toLocaleDateString()}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {showCreate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-40 p-4">
          <div className="bg-white rounded-lg shadow-xl p-6 max-w-md w-full">
            <h2 className="text-lg font-semibold mb-4">Create Service Account</h2>
            <label className="block text-sm font-medium mb-1">Display name</label>
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm mb-3"
              placeholder="Benchling Sync"
            />
            <label className="block text-sm font-medium mb-1">Role</label>
            <select
              value={newRoleId ?? ""}
              onChange={(e) => setNewRoleId(Number(e.target.value) || null)}
              className="w-full border rounded px-3 py-2 text-sm mb-4"
            >
              <option value="">Select a role</option>
              {roles.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
            <div className="flex justify-end gap-2">
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
            </div>
          </div>
        </div>
      )}

      {selectedSa && (
        <div className="fixed inset-0 bg-black/40 flex justify-end z-30">
          <div className="bg-white w-full max-w-xl h-full overflow-y-auto p-6 shadow-xl">
            <div className="flex justify-between items-start mb-4">
              <div>
                <h2 className="text-lg font-semibold">{selectedSa.display_name}</h2>
                <p className="text-xs text-gray-500 font-mono">{selectedSa.email}</p>
              </div>
              <button
                onClick={() => setSelectedSa(null)}
                className="text-gray-400 hover:text-gray-600"
              >
                Close
              </button>
            </div>

            <div className="mb-4">
              <h3 className="text-sm font-semibold mb-2">API Keys</h3>
              <div className="flex justify-end mb-2">
                <button
                  onClick={() => setShowMintKey(true)}
                  className="px-3 py-1.5 text-xs bg-bioaf-600 text-white rounded hover:bg-bioaf-700"
                >
                  Mint key
                </button>
              </div>
              {keysLoading ? (
                <LoadingSpinner size="sm" />
              ) : keys.length === 0 ? (
                <p className="text-sm text-gray-500">No keys yet.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                      <th className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase">Prefix</th>
                      <th className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                      <th className="px-2 py-2"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {keys.map((k) => (
                      <tr key={k.id}>
                        <td className="px-2 py-2">{k.name}</td>
                        <td className="px-2 py-2 font-mono text-xs">biokey_{k.key_prefix}</td>
                        <td className="px-2 py-2">{k.revoked_at ? "revoked" : "active"}</td>
                        <td className="px-2 py-2">
                          {!k.revoked_at && (
                            <button
                              onClick={() => handleRevoke(k.id)}
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

            <button
              onClick={() => handleDisableSa(selectedSa)}
              className="px-3 py-2 text-sm bg-red-600 text-white rounded hover:bg-red-700"
            >
              Disable Service Account
            </button>
          </div>
        </div>
      )}

      {showMintKey && selectedSa && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl p-6 max-w-lg w-full">
            <h2 className="text-lg font-semibold mb-4">Mint API Key</h2>
            <label className="block text-sm font-medium mb-1">Name</label>
            <input
              value={newKeyName}
              onChange={(e) => setNewKeyName(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm mb-4"
              placeholder="Primary"
            />
            <label className="block text-sm font-medium mb-2">Scopes</label>
            <div className="space-y-2 mb-4 max-h-64 overflow-y-auto border rounded p-2">
              {Object.entries(groupedScopes).map(([resource, scopes]) => (
                <div key={resource}>
                  <div className="text-xs uppercase text-gray-500 font-semibold mt-2 mb-1">
                    {resource}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {scopes.map((s) => (
                      <label key={s} className="flex items-center gap-1 text-xs">
                        <input
                          type="checkbox"
                          checked={newKeyScopes.has(s)}
                          onChange={(e) => {
                            const next = new Set(newKeyScopes);
                            if (e.target.checked) next.add(s);
                            else next.delete(s);
                            setNewKeyScopes(next);
                          }}
                        />
                        <span>{s}</span>
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowMintKey(false)}
                className="px-3 py-2 text-sm bg-gray-200 rounded hover:bg-gray-300"
              >
                Cancel
              </button>
              <button
                onClick={handleMintKey}
                disabled={!newKeyName.trim() || newKeyScopes.size === 0}
                className="px-3 py-2 text-sm bg-bioaf-600 text-white rounded hover:bg-bioaf-700 disabled:opacity-50"
              >
                Mint
              </button>
            </div>
          </div>
        </div>
      )}

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
