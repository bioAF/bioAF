"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { InviteForm } from "@/components/auth/InviteForm";
import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { api, ApiError } from "@/lib/api";
import type { User, Role, RoleListResponse } from "@/lib/types";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { DetailModal } from "@/components/shared/DetailModal";

export default function UsersPage() {
  const router = useRouter();
  const { canAccess, loading: permLoading } = usePermissions();
  const [users, setUsers] = useState<User[]>([]);
  const [viewingUser, setViewingUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [showInvite, setShowInvite] = useState(false);
  const [error, setError] = useState("");
  const [roles, setRoles] = useState<Role[]>([]);

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    if (permLoading) return;
    if (!canAccess("users", "view")) { router.push("/dashboard"); return; }
    fetchUsers();
    api.get<RoleListResponse>("/api/roles")
      .then((data) => setRoles(data.roles))
      .catch(() => {});
  }, [router, permLoading, canAccess]);

  const fetchUsers = async () => {
    try {
      const data = await api.get<{ users: User[]; total: number }>("/api/users");
      setUsers(data.users);
    } catch { /* handled */ } finally {
      setLoading(false);
    }
  };

  const handleDeactivate = async (userId: number) => {
    setError("");
    try {
      await api.post(`/api/users/${userId}/deactivate`);
      fetchUsers();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to deactivate user");
    }
  };

  const handleRoleChange = async (userId: number, roleName: string) => {
    const targetRole = roles.find((r) => r.name === roleName);
    if (!targetRole) return;
    try {
      await api.patch(`/api/users/${userId}`, { role_id: targetRole.id });
      fetchUsers();
    } catch { /* handled */ }
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-2xl font-bold">Users & Roles</h1>
            <button onClick={() => setShowInvite(!showInvite)} className="px-4 py-2 bg-bioaf-600 text-white rounded hover:bg-bioaf-700">
              {showInvite ? "Close" : "Invite Users"}
            </button>
          </div>

          {error && <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm">{error}</div>}

          {showInvite && (
            <div className="bg-white rounded-lg shadow p-6 mb-6">
              <h2 className="text-lg font-semibold mb-4">Invite Users</h2>
              <InviteForm roles={roles} />
            </div>
          )}

          {loading ? <LoadingSpinner size="lg" /> : (
            <div className="bg-white rounded-lg shadow overflow-hidden">
              <table className="w-full">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Email</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Role</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {users.map((user) => (
                    <tr key={user.id} className="hover:bg-gray-50 cursor-pointer" onClick={() => setViewingUser(user)}>
                      <td className="px-6 py-4 text-sm">{user.email}</td>
                      <td className="px-6 py-4 text-sm text-gray-500">{user.name || "—"}</td>
                      <td className="px-6 py-4" onClick={(e) => e.stopPropagation()}>
                        <select
                          value={user.role_name}
                          onChange={(e) => handleRoleChange(user.id, e.target.value)}
                          className="text-sm border rounded px-2 py-1"
                        >
                          {roles.map((r) => (
                            <option key={r.id} value={r.name}>{r.name}</option>
                          ))}
                        </select>
                      </td>
                      <td className="px-6 py-4"><StatusBadge status={user.status} /></td>
                      <td className="px-6 py-4" onClick={(e) => e.stopPropagation()}>
                        {user.status === "active" && (
                          <button onClick={() => handleDeactivate(user.id)} className="text-xs px-2 py-1 border border-red-600 text-red-600 rounded hover:bg-red-50">
                            Deactivate
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {viewingUser && (
            <DetailModal
              title={viewingUser.name || viewingUser.email}
              onClose={() => setViewingUser(null)}
              fields={[
                { label: "Email", value: viewingUser.email },
                { label: "Name", value: viewingUser.name },
                { label: "Role", value: viewingUser.role_name },
                { label: "Status", value: viewingUser.status },
                { label: "Created", value: new Date(viewingUser.created_at).toLocaleString() },
                { label: "Updated", value: new Date(viewingUser.updated_at).toLocaleString() },
              ]}
              actions={
                viewingUser.status === "active" ? (
                  <button
                    onClick={() => { handleDeactivate(viewingUser.id); setViewingUser(null); }}
                    className="px-3 py-1.5 border border-red-600 text-red-600 rounded text-sm hover:bg-red-50"
                  >
                    Deactivate
                  </button>
                ) : undefined
              }
            />
          )}
        </main>
      </div>
    </div>
  );
}
