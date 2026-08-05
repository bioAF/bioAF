"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { PermissionEntry, Role } from "@/lib/types";

export type PermissionCatalog = Record<string, string[]>;

interface Props {
  editingRole: Role | null;
  catalog: PermissionCatalog;
  onClose: () => void;
  onSaved: (saved: Role) => void;
}

export function RoleEditorModal({ editingRole, catalog, onClose, onSaved }: Props) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [permissions, setPermissions] = useState<Record<string, Set<string>>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (editingRole) {
      setName(editingRole.name);
      setDescription(editingRole.description || "");
      const perms: Record<string, Set<string>> = {};
      for (const p of editingRole.permissions) {
        if (!perms[p.resource]) perms[p.resource] = new Set();
        perms[p.resource].add(p.action);
      }
      setPermissions(perms);
    } else {
      setName("");
      setDescription("");
      setPermissions({});
    }
    setError("");
  }, [editingRole]);

  function togglePermission(resource: string, action: string) {
    setPermissions((prev) => {
      const next = { ...prev };
      next[resource] = new Set(prev[resource] || []);
      if (next[resource].has(action)) {
        next[resource].delete(action);
        if (next[resource].size === 0) delete next[resource];
      } else {
        next[resource].add(action);
      }
      return next;
    });
  }

  function toggleAllForResource(resource: string, actions: string[]) {
    setPermissions((prev) => {
      const next = { ...prev };
      const current = prev[resource] || new Set();
      if (actions.every((a) => current.has(a))) {
        delete next[resource];
      } else {
        next[resource] = new Set(actions);
      }
      return next;
    });
  }

  function buildPermissionList(): PermissionEntry[] {
    const out: PermissionEntry[] = [];
    for (const [resource, actions] of Object.entries(permissions)) {
      for (const action of actions) out.push({ resource, action });
    }
    return out;
  }

  async function handleSave() {
    if (!name.trim()) {
      setError("Role name is required");
      return;
    }
    setSaving(true);
    setError("");
    try {
      let saved: Role;
      if (editingRole) {
        await api.patch(`/api/roles/${editingRole.id}`, {
          name: name.trim(),
          description: description.trim() || null,
        });
        await api.put(`/api/roles/${editingRole.id}/permissions`, {
          permissions: buildPermissionList(),
        });
        saved = { ...editingRole, name: name.trim(), description: description.trim() || null };
      } else {
        saved = await api.post<Role>("/api/roles", {
          name: name.trim(),
          description: description.trim() || null,
          permissions: buildPermissionList(),
        });
      }
      onSaved(saved);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-y-auto">
        <div className="p-6 border-b">
          <h2 className="text-lg font-semibold">
            {editingRole ? `Edit Role: ${editingRole.name}` : "Create New Role"}
          </h2>
        </div>
        <div className="p-6 space-y-4">
          {error && (
            <div className="p-3 bg-red-50 text-red-700 text-sm rounded border border-red-200">{error}</div>
          )}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="name" className="block text-sm font-medium text-gray-700 mb-1">Name</label>
              <input id="name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm"
                placeholder="e.g. data_analyst"
              />
            </div>
            <div>
              <label htmlFor="description" className="block text-sm font-medium text-gray-700 mb-1">Description</label>
              <input id="description"
                type="text"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm"
                placeholder="Optional description"
              />
            </div>
          </div>

          <div>
            <h3 className="text-sm font-semibold text-gray-700 mb-2">Permissions</h3>
            <div className="border rounded divide-y max-h-96 overflow-y-auto">
              {Object.entries(catalog).sort(([a], [b]) => a.localeCompare(b)).map(([resource, actions]) => {
                const selected = permissions[resource] || new Set();
                const allSelected = actions.every((a) => selected.has(a));
                return (
                  <div key={resource} className="p-3">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium text-gray-800">{resource}</span>
                      <button
                        type="button"
                        onClick={() => toggleAllForResource(resource, actions)}
                        className="text-xs text-bioaf-600 hover:underline"
                      >
                        {allSelected ? "Deselect all" : "Select all"}
                      </button>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {actions.sort().map((action) => (
                        <label key={action} className="flex items-center gap-1 text-xs cursor-pointer">
                          <input
                            type="checkbox"
                            checked={selected.has(action)}
                            onChange={() => togglePermission(resource, action)}
                            className="rounded border-gray-300"
                          />
                          <span className="text-gray-700">{action}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
        <div className="p-6 border-t flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm border rounded hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 text-sm bg-bioaf-600 text-white rounded hover:bg-bioaf-700 disabled:opacity-50"
          >
            {saving ? "Saving..." : editingRole ? "Save Changes" : "Create Role"}
          </button>
        </div>
      </div>
    </div>
  );
}
