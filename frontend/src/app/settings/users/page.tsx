"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { InviteForm } from "@/components/auth/InviteForm";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import { api, ApiError } from "@/lib/api";
import type { User } from "@/lib/types";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { DetailModal } from "@/components/shared/DetailModal";

interface NeverLoggedInUser {
  id: number;
  email: string;
  name: string | null;
  role: string;
  status: string;
  created_at: string | null;
}

type PendingAction =
  | { type: "deactivate"; user: User }
  | { type: "lock"; user: User }
  | { type: "role_change"; user: User; newRole: string }
  | { type: "resend_invite"; user: User }
  | { type: "reset_password_email"; user: User };

export default function SettingsUsersPage() {
  const router = useRouter();
  const [users, setUsers] = useState<User[]>([]);
  const [viewingUser, setViewingUser] = useState<User | null>(null);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [editName, setEditName] = useState("");
  const [editRole, setEditRole] = useState("");
  const [loading, setLoading] = useState(true);
  const [showInvite, setShowInvite] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [neverLoggedIn, setNeverLoggedIn] = useState<NeverLoggedInUser[]>([]);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [tempPassword, setTempPassword] = useState("");
  const [showTempPasswordForm, setShowTempPasswordForm] = useState(false);
  const [tempPasswordUser, setTempPasswordUser] = useState<User | null>(null);
  const [smtpConfigured, setSmtpConfigured] = useState(false);

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    const user = getCurrentUser();
    if (user?.role !== "admin") { router.push("/"); return; }
    fetchUsers();
    api.get<{ users: NeverLoggedInUser[] }>("/api/access-logs/never-logged-in")
      .then((data) => setNeverLoggedIn(data.users))
      .catch(() => {});
    api.get<{ setup_complete: boolean; smtp_configured: boolean }>("/api/bootstrap/status")
      .then((data) => setSmtpConfigured(data.smtp_configured))
      .catch(() => {});
  }, [router]);

  const fetchUsers = async () => {
    try {
      const data = await api.get<{ users: User[]; total: number }>("/api/users");
      setUsers(data.users);
    } catch { /* handled */ } finally {
      setLoading(false);
    }
  };

  const clearMessages = () => { setError(""); setSuccess(""); };

  const handleConfirmAction = async () => {
    if (!pendingAction) return;
    clearMessages();

    try {
      switch (pendingAction.type) {
        case "deactivate":
          await api.post(`/api/users/${pendingAction.user.id}/deactivate`);
          setSuccess(`${pendingAction.user.email} deactivated`);
          break;
        case "lock":
          await api.post(`/api/users/${pendingAction.user.id}/lock`);
          setSuccess(`${pendingAction.user.email} locked`);
          break;
        case "role_change":
          await api.patch(`/api/users/${pendingAction.user.id}`, { role: pendingAction.newRole });
          setSuccess(`${pendingAction.user.email} role changed to ${pendingAction.newRole}`);
          break;
        case "resend_invite":
          await api.post(`/api/users/${pendingAction.user.id}/resend-invite`);
          setSuccess(`Invitation resent to ${pendingAction.user.email}`);
          break;
        case "reset_password_email":
          await api.post(`/api/users/${pendingAction.user.id}/admin-reset-password`, { mode: "email" });
          setSuccess(`Password reset email sent to ${pendingAction.user.email}`);
          break;
      }
      fetchUsers();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Action failed");
    }
    setPendingAction(null);
  };

  const handleSetTempPassword = async () => {
    if (!tempPasswordUser || !tempPassword) return;
    clearMessages();
    try {
      await api.post(`/api/users/${tempPasswordUser.id}/admin-reset-password`, {
        mode: "temporary",
        temporary_password: tempPassword,
      });
      setSuccess(`Password changed for ${tempPasswordUser.email}`);
      fetchUsers();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to set password");
    }
    setTempPassword("");
    setTempPasswordUser(null);
    setShowTempPasswordForm(false);
  };

  const handleEditSave = async () => {
    if (!editingUser) return;
    clearMessages();

    const updates: Record<string, string> = {};
    if (editName !== (editingUser.name || "")) updates.name = editName;
    if (editRole !== editingUser.role) updates.role = editRole;

    if (Object.keys(updates).length === 0) {
      setEditingUser(null);
      return;
    }

    // If role is changing, require confirmation
    if (updates.role) {
      setPendingAction({ type: "role_change", user: editingUser, newRole: editRole });
      setEditingUser(null);
      return;
    }

    try {
      await api.patch(`/api/users/${editingUser.id}`, updates);
      setSuccess(`${editingUser.email} updated`);
      fetchUsers();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to update user");
    }
    setEditingUser(null);
  };

  const getConfirmMessage = (): { title: string; message: string; variant: "danger" | "default" } => {
    if (!pendingAction) return { title: "", message: "", variant: "default" };
    switch (pendingAction.type) {
      case "deactivate":
        return {
          title: "Deactivate User",
          message: `Are you sure you want to deactivate ${pendingAction.user.email}? They will lose access. Their data is retained.`,
          variant: "danger",
        };
      case "lock":
        return {
          title: "Lock User",
          message: `Lock ${pendingAction.user.email}? This disables their login until unlocked.`,
          variant: "danger",
        };
      case "role_change":
        return {
          title: "Change User Role",
          message: `Change ${pendingAction.user.email} from ${pendingAction.user.role} to ${pendingAction.newRole}?`,
          variant: "default",
        };
      case "resend_invite":
        return {
          title: "Resend Invitation",
          message: `Resend the invitation email to ${pendingAction.user.email}?`,
          variant: "default",
        };
      case "reset_password_email":
        return {
          title: "Send Password Reset",
          message: `Send a password reset email to ${pendingAction.user.email}?`,
          variant: "default",
        };
      default:
        return { title: "", message: "", variant: "default" };
    }
  };

  const formatLastLogin = (lastLogin: string | null): string => {
    if (!lastLogin) return "Never";
    const date = new Date(lastLogin);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    if (diffDays === 0) return "Today";
    if (diffDays === 1) return "Yesterday";
    if (diffDays < 7) return `${diffDays} days ago`;
    return date.toLocaleDateString();
  };

  const renderUserActions = (user: User) => {
    const isDeactivated = user.status === "deactivated";

    return (
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => {
            setEditingUser(user);
            setEditName(user.name || "");
            setEditRole(user.role);
            setViewingUser(null);
          }}
          className="px-3 py-1.5 text-sm bg-bioaf-600 text-white rounded hover:bg-bioaf-700"
        >
          Edit
        </button>
        {user.status === "invited" && (
          <button
            onClick={() => {
              setPendingAction({ type: "resend_invite", user });
              setViewingUser(null);
            }}
            className="px-3 py-1.5 text-sm bg-white border border-gray-300 text-gray-700 rounded hover:bg-gray-50"
          >
            Resend Invite
          </button>
        )}
        {!isDeactivated && !smtpConfigured && (
          <button
            onClick={() => {
              setTempPasswordUser(user);
              setShowTempPasswordForm(true);
              setViewingUser(null);
            }}
            className="px-3 py-1.5 text-sm bg-white border border-gray-300 text-gray-700 rounded hover:bg-gray-50"
          >
            Change Password
          </button>
        )}
        {!isDeactivated && smtpConfigured && (
          <button
            onClick={() => {
              setPendingAction({ type: "reset_password_email", user });
              setViewingUser(null);
            }}
            className="px-3 py-1.5 text-sm bg-white border border-gray-300 text-gray-700 rounded hover:bg-gray-50"
          >
            Send Password Reset Link
          </button>
        )}
        {!isDeactivated && (
          <button
            onClick={() => {
              setPendingAction({ type: "lock", user });
              setViewingUser(null);
            }}
            className="px-3 py-1.5 text-sm bg-white border border-amber-300 text-amber-700 rounded hover:bg-amber-50"
          >
            Lock
          </button>
        )}
        {!isDeactivated && (
          <button
            onClick={() => {
              setPendingAction({ type: "deactivate", user });
              setViewingUser(null);
            }}
            className="px-3 py-1.5 text-sm bg-white border border-red-300 text-red-600 rounded hover:bg-red-50"
          >
            Deactivate
          </button>
        )}
      </div>
    );
  };

  const confirm = getConfirmMessage();

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-2xl font-bold">Users & Roles</h1>
            <button
              onClick={() => setShowInvite(!showInvite)}
              className="px-4 py-2 bg-bioaf-600 text-white rounded hover:bg-bioaf-700"
            >
              {showInvite ? "Close" : "Invite Users"}
            </button>
          </div>

          {error && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm">
              {error}
            </div>
          )}
          {success && (
            <div className="mb-4 p-3 bg-green-50 border border-green-200 text-green-700 rounded text-sm">
              {success}
            </div>
          )}

          {neverLoggedIn.length > 0 && (
            <div className="mb-4 p-4 bg-amber-50 border border-amber-200 rounded-lg">
              <h3 className="text-sm font-semibold text-amber-800 mb-2">
                Users who have never logged in ({neverLoggedIn.length})
              </h3>
              <ul className="text-sm text-amber-700 space-y-1">
                {neverLoggedIn.map((u) => (
                  <li key={u.id}>
                    {u.email}
                    {u.role !== "viewer" && <span className="ml-2 text-amber-500">({u.role})</span>}
                    {u.created_at && (
                      <span className="ml-2 text-amber-400 text-xs">
                        invited {new Date(u.created_at).toLocaleDateString()}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {showInvite && (
            <div className="bg-white rounded-lg shadow p-6 mb-6">
              <h2 className="text-lg font-semibold mb-4">Invite Users</h2>
              <InviteForm />
            </div>
          )}

          {loading ? (
            <LoadingSpinner size="lg" />
          ) : (
            <div className="bg-white rounded-lg shadow overflow-hidden">
              <table className="w-full">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                      Email
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                      Name
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                      Role
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                      Status
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                      Last Login
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                      Session Keys
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {users.map((user) => (
                    <tr
                      key={user.id}
                      className="hover:bg-gray-50 cursor-pointer"
                      onClick={() => setViewingUser(user)}
                    >
                      <td className="px-4 py-3 text-sm">{user.email}</td>
                      <td className="px-4 py-3 text-sm text-gray-500">
                        {user.name || "\u2014"}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-700">
                          {user.role}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={user.status} />
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-500">
                        {formatLastLogin(user.last_login)}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {user.session_credentials_configured ? (
                          <span className="text-green-600 text-sm" title="Session credentials configured">
                            &#10003;
                          </span>
                        ) : (
                          <span className="text-gray-300 text-sm" title="Not configured">
                            &#8212;
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Confirmation dialog */}
          {pendingAction && (
            <ConfirmDialog
              open={true}
              title={confirm.title}
              message={confirm.message}
              variant={confirm.variant}
              confirmLabel={
                pendingAction.type === "deactivate" ? "Deactivate"
                  : pendingAction.type === "lock" ? "Lock"
                    : "Confirm"
              }
              onConfirm={handleConfirmAction}
              onCancel={() => setPendingAction(null)}
            />
          )}

          {/* Change password form modal */}
          {showTempPasswordForm && tempPasswordUser && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
              <div className="bg-white rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
                <h3 className="text-lg font-semibold mb-2">Change Password</h3>
                <p className="text-sm text-gray-600 mb-4">
                  Set a new password for {tempPasswordUser.email}. They should change
                  it after logging in.
                </p>
                <input
                  type="password"
                  value={tempPassword}
                  onChange={(e) => setTempPassword(e.target.value)}
                  placeholder="Enter new password"
                  className="w-full px-3 py-2 border rounded-md text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-bioaf-500"
                />
                <div className="flex justify-end gap-3">
                  <button
                    onClick={() => {
                      setShowTempPasswordForm(false);
                      setTempPasswordUser(null);
                      setTempPassword("");
                    }}
                    className="px-4 py-2 text-sm text-gray-700 bg-gray-100 rounded hover:bg-gray-200"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleSetTempPassword}
                    disabled={!tempPassword}
                    className="px-4 py-2 text-sm text-white bg-bioaf-600 rounded hover:bg-bioaf-700 disabled:opacity-50"
                  >
                    Set Password
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Edit user modal */}
          {editingUser && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
              <div className="bg-white rounded-lg shadow-xl max-w-md w-full mx-4 p-6">
                <h3 className="text-lg font-semibold mb-4">
                  Edit {editingUser.email}
                </h3>
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Name
                    </label>
                    <input
                      type="text"
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-bioaf-500"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      Role
                    </label>
                    <select
                      value={editRole}
                      onChange={(e) => setEditRole(e.target.value)}
                      className="w-full px-3 py-2 border rounded-md text-sm"
                    >
                      <option value="admin">Admin</option>
                      <option value="comp_bio">Comp Bio</option>
                      <option value="bench">Bench</option>
                      <option value="viewer">Viewer</option>
                    </select>
                  </div>
                </div>
                <div className="flex justify-end gap-3 mt-6">
                  <button
                    onClick={() => setEditingUser(null)}
                    className="px-4 py-2 text-sm text-gray-700 bg-gray-100 rounded hover:bg-gray-200"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleEditSave}
                    className="px-4 py-2 text-sm text-white bg-bioaf-600 rounded hover:bg-bioaf-700"
                  >
                    Save Changes
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Detail modal */}
          {viewingUser && (
            <DetailModal
              title={viewingUser.name || viewingUser.email}
              onClose={() => setViewingUser(null)}
              fields={[
                { label: "Email", value: viewingUser.email },
                { label: "Name", value: viewingUser.name },
                { label: "Role", value: viewingUser.role },
                { label: "Status", value: viewingUser.status },
                {
                  label: "Last Login",
                  value: formatLastLogin(viewingUser.last_login),
                },
                {
                  label: "Session Keys",
                  value: viewingUser.session_credentials_configured
                    ? "Configured"
                    : "Not configured",
                },
                {
                  label: "Created",
                  value: new Date(viewingUser.created_at).toLocaleString(),
                },
                {
                  label: "Updated",
                  value: new Date(viewingUser.updated_at).toLocaleString(),
                },
              ]}
              actions={renderUserActions(viewingUser)}
            />
          )}
        </main>
      </div>
    </div>
  );
}
