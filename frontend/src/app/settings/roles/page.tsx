"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { api, ApiError } from "@/lib/api";
import type { Role, RoleListResponse, PermissionEntry } from "@/lib/types";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

type PermissionCatalog = Record<string, string[]>;

export default function SettingsRolesPage() {
  const router = useRouter();
  const { canAccess, loading: permLoading } = usePermissions();
  const [roles, setRoles] = useState<Role[]>([]);
  const [catalog, setCatalog] = useState<PermissionCatalog>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Create/edit form
  const [showForm, setShowForm] = useState(false);
  const [editingRole, setEditingRole] = useState<Role | null>(null);
  const [formName, setFormName] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formPermissions, setFormPermissions] = useState<Record<string, Set<string>>>({});
  const [saving, setSaving] = useState(false);

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<Role | null>(null);

  // Expanded role detail
  const [expandedRoleId, setExpandedRoleId] = useState<number | null>(null);

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    if (permLoading) return;
    if (!canAccess("roles", "view")) { router.push("/dashboard"); return; }
    loadData();
  }, [router, permLoading, canAccess]);

  async function loadData() {
    try {
      const [rolesData, catalogData] = await Promise.all([
        api.get<RoleListResponse>("/api/roles"),
        api.get<PermissionCatalog>("/api/roles/permissions-catalog"),
      ]);
      setRoles(rolesData.roles);
      setCatalog(catalogData);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load roles");
    } finally {
      setLoading(false);
    }
  }

  function openCreateForm() {
    setEditingRole(null);
    setFormName("");
    setFormDescription("");
    setFormPermissions({});
    setShowForm(true);
    setError("");
    setSuccess("");
  }

  function openEditForm(role: Role) {
    setEditingRole(role);
    setFormName(role.name);
    setFormDescription(role.description || "");
    const perms: Record<string, Set<string>> = {};
    for (const p of role.permissions) {
      if (!perms[p.resource]) perms[p.resource] = new Set();
      perms[p.resource].add(p.action);
    }
    setFormPermissions(perms);
    setShowForm(true);
    setError("");
    setSuccess("");
  }

  function togglePermission(resource: string, action: string) {
    setFormPermissions((prev) => {
      const next = { ...prev };
      if (!next[resource]) next[resource] = new Set();
      else next[resource] = new Set(next[resource]);
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
    setFormPermissions((prev) => {
      const next = { ...prev };
      const current = prev[resource] || new Set();
      const allSelected = actions.every((a) => current.has(a));
      if (allSelected) {
        delete next[resource];
      } else {
        next[resource] = new Set(actions);
      }
      return next;
    });
  }

  function buildPermissionList(): PermissionEntry[] {
    const perms: PermissionEntry[] = [];
    for (const [resource, actions] of Object.entries(formPermissions)) {
      for (const action of actions) {
        perms.push({ resource, action });
      }
    }
    return perms;
  }

  async function handleSave() {
    if (!formName.trim()) { setError("Role name is required"); return; }
    setSaving(true);
    setError("");
    try {
      if (editingRole) {
        await api.patch(`/api/roles/${editingRole.id}`, {
          name: formName.trim(),
          description: formDescription.trim() || null,
        });
        await api.put(`/api/roles/${editingRole.id}/permissions`, {
          permissions: buildPermissionList(),
        });
        setSuccess(`Role "${formName}" updated`);
      } else {
        await api.post("/api/roles", {
          name: formName.trim(),
          description: formDescription.trim() || null,
          permissions: buildPermissionList(),
        });
        setSuccess(`Role "${formName}" created`);
      }
      setShowForm(false);
      await loadData();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    try {
      await api.delete(`/api/roles/${deleteTarget.id}`);
      setSuccess(`Role "${deleteTarget.name}" deleted`);
      setDeleteTarget(null);
      await loadData();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Delete failed");
      setDeleteTarget(null);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col">
          <Header />
          <main className="flex-1 p-6 flex items-center justify-center">
            <LoadingSpinner />
          </main>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen bg-gray-50">
      <Sidebar />
      <div className="flex-1 flex flex-col">
        <Header />
        <main className="flex-1 p-6">
          <div className="max-w-6xl mx-auto">
            <div className="flex items-center justify-between mb-6">
              <div>
                <h1 className="text-2xl font-bold text-gray-900">Roles & Permissions</h1>
                <p className="text-sm text-gray-500 mt-1">
                  Manage roles and their permission assignments
                </p>
              </div>
              <button
                onClick={openCreateForm}
                className="px-4 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700"
              >
                Create Role
              </button>
            </div>

            {error && (
              <div className="mb-4 p-3 bg-red-50 text-red-700 text-sm rounded border border-red-200">
                {error}
              </div>
            )}
            {success && (
              <div className="mb-4 p-3 bg-green-50 text-green-700 text-sm rounded border border-green-200">
                {success}
              </div>
            )}

            {/* Role list */}
            <div className="bg-white rounded-lg shadow overflow-hidden">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Role</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Description</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Permissions</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
                    <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {roles.map((role) => (
                    <tr key={role.id} className="hover:bg-gray-50">
                      <td className="px-6 py-4">
                        <button
                          className="text-sm font-medium text-blue-600 hover:underline"
                          onClick={() => setExpandedRoleId(expandedRoleId === role.id ? null : role.id)}
                        >
                          {role.name}
                        </button>
                      </td>
                      <td className="px-6 py-4 text-sm text-gray-500">{role.description || "--"}</td>
                      <td className="px-6 py-4 text-sm text-gray-500">{role.permissions.length}</td>
                      <td className="px-6 py-4">
                        <span className={`inline-flex text-xs px-2 py-0.5 rounded-full ${
                          role.is_system
                            ? "bg-gray-100 text-gray-600"
                            : "bg-blue-100 text-blue-700"
                        }`}>
                          {role.is_system ? "System" : "Custom"}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-right space-x-2">
                        {!role.is_system && (
                          <>
                            <button
                              onClick={() => openEditForm(role)}
                              className="text-sm text-blue-600 hover:underline"
                            >
                              Edit
                            </button>
                            <button
                              onClick={() => setDeleteTarget(role)}
                              className="text-sm text-red-600 hover:underline"
                            >
                              Delete
                            </button>
                          </>
                        )}
                        {role.is_system && (
                          <span className="text-xs text-gray-400">Built-in</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Expanded role permissions */}
            {expandedRoleId && (() => {
              const role = roles.find((r) => r.id === expandedRoleId);
              if (!role) return null;
              const grouped: Record<string, string[]> = {};
              for (const p of role.permissions) {
                if (!grouped[p.resource]) grouped[p.resource] = [];
                grouped[p.resource].push(p.action);
              }
              return (
                <div className="mt-4 bg-white rounded-lg shadow p-6">
                  <h3 className="text-sm font-semibold text-gray-700 mb-3">
                    Permissions for &ldquo;{role.name}&rdquo;
                  </h3>
                  <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
                    {Object.entries(grouped).sort(([a], [b]) => a.localeCompare(b)).map(([resource, actions]) => (
                      <div key={resource} className="border rounded p-2">
                        <div className="text-xs font-medium text-gray-700 mb-1">{resource}</div>
                        <div className="flex flex-wrap gap-1">
                          {actions.sort().map((action) => (
                            <span key={action} className="text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">
                              {action}
                            </span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}

            {/* Create/Edit modal */}
            {showForm && (
              <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
                <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-y-auto">
                  <div className="p-6 border-b">
                    <h2 className="text-lg font-semibold">
                      {editingRole ? `Edit Role: ${editingRole.name}` : "Create New Role"}
                    </h2>
                  </div>
                  <div className="p-6 space-y-4">
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                        <input
                          type="text"
                          value={formName}
                          onChange={(e) => setFormName(e.target.value)}
                          className="w-full border rounded px-3 py-2 text-sm"
                          placeholder="e.g. data_analyst"
                        />
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                        <input
                          type="text"
                          value={formDescription}
                          onChange={(e) => setFormDescription(e.target.value)}
                          className="w-full border rounded px-3 py-2 text-sm"
                          placeholder="Optional description"
                        />
                      </div>
                    </div>

                    <div>
                      <h3 className="text-sm font-semibold text-gray-700 mb-2">Permissions</h3>
                      <div className="border rounded divide-y max-h-96 overflow-y-auto">
                        {Object.entries(catalog).sort(([a], [b]) => a.localeCompare(b)).map(([resource, actions]) => {
                          const selected = formPermissions[resource] || new Set();
                          const allSelected = actions.every((a) => selected.has(a));
                          return (
                            <div key={resource} className="p-3">
                              <div className="flex items-center justify-between mb-2">
                                <span className="text-sm font-medium text-gray-800">{resource}</span>
                                <button
                                  type="button"
                                  onClick={() => toggleAllForResource(resource, actions)}
                                  className="text-xs text-blue-600 hover:underline"
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
                      onClick={() => setShowForm(false)}
                      className="px-4 py-2 text-sm border rounded hover:bg-gray-50"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleSave}
                      disabled={saving}
                      className="px-4 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
                    >
                      {saving ? "Saving..." : editingRole ? "Save Changes" : "Create Role"}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Delete confirm */}
            {deleteTarget && (
              <ConfirmDialog
                open={true}
                title="Delete Role"
                message={`Are you sure you want to delete the role "${deleteTarget.name}"? This cannot be undone.`}
                confirmLabel="Delete"
                onConfirm={handleDelete}
                onCancel={() => setDeleteTarget(null)}
              />
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
