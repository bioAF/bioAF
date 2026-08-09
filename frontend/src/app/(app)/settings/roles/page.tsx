"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { usePermissions } from "@/hooks/usePermissions";
import { api, ApiError } from "@/lib/api";
import type { Role, RoleListResponse } from "@/lib/types";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { RoleEditorModal, type PermissionCatalog } from "@/components/settings/RoleEditorModal";
import { Card } from "@/components/ui/Card";

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

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<Role | null>(null);

  // Expanded role detail
  const [expandedRoleId, setExpandedRoleId] = useState<number | null>(null);

  useEffect(() => {
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
    setShowForm(true);
    setError("");
    setSuccess("");
  }

  function openEditForm(role: Role) {
    setEditingRole(role);
    setShowForm(true);
    setError("");
    setSuccess("");
  }

  async function handleRoleSaved(saved: Role) {
    setShowForm(false);
    setSuccess(`Role "${saved.name}" ${editingRole ? "updated" : "created"}`);
    await loadData();
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
      <main className="flex-1 p-6 flex items-center justify-center">
        <LoadingSpinner />
      </main>
    );
  }

  return (
    <main className="flex-1 p-6">
      <div className="max-w-6xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Roles & Permissions</h1>
            <p data-testid="page-description" className="text-sm text-gray-500 mt-1">
              Manage roles and their permission assignments
            </p>
          </div>
          <button
            onClick={openCreateForm}
            className="px-4 py-2 bg-bioaf-600 text-white text-sm rounded hover:bg-bioaf-700"
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
        <Card padding="none" className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Role</th>
                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Description</th>
                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Permissions</th>
                <th scope="col" className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
                <th scope="col" className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {roles.map((role) => (
                <tr key={role.id} className="hover:bg-gray-50">
                  <td className="px-6 py-4">
                    <button
                      className="text-sm font-medium text-bioaf-600 hover:underline"
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
                          className="text-sm text-bioaf-600 hover:underline"
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
                      <span className="text-xs text-gray-500">Built-in</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

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

        {showForm && (
          <RoleEditorModal
            editingRole={editingRole}
            catalog={catalog}
            onClose={() => setShowForm(false)}
            onSaved={handleRoleSaved}
          />
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
  );
}
