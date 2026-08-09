"use client";

import { NOT_SET } from "@/lib/placeholders";
import { Suspense, useEffect, useState } from "react";
import { Modal } from "@/components/shared/Modal";
import { useRouter, useSearchParams } from "next/navigation";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { InviteForm } from "@/components/auth/InviteForm";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { usePermissions } from "@/hooks/usePermissions";
import { api, ApiError } from "@/lib/api";
import type { User, Role, RoleListResponse } from "@/lib/types";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { DetailModal } from "@/components/shared/DetailModal";
import { ServiceAccountsTab } from "./components/ServiceAccountsTab";
import { WebhooksTab } from "./components/WebhooksTab";
import { ApiActivityTab } from "./components/ApiActivityTab";
import { PasswordResetActions } from "./components/PasswordResetActions";

import { clickableRow } from "@/lib/a11y";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { useToast } from "@/components/shared/Toast";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";

type TabKey = "users" | "service-accounts" | "webhooks" | "api-activity";

const TAB_LABELS: Record<TabKey, string> = {
  users: "Users",
  "service-accounts": "Service Accounts",
  webhooks: "Webhooks",
  "api-activity": "API Activity",
};

interface NeverLoggedInUser {
  id: number;
  email: string;
  name: string | null;
  role_name: string | null;
  status: string;
  created_at: string | null;
}

type PendingAction =
  | { type: "deactivate"; user: User }
  | { type: "lock"; user: User }
  | { type: "role_change"; user: User; newRole: string }
  | { type: "resend_invite"; user: User }
  | { type: "reset_password_email"; user: User }
  | { type: "reactivate"; user: User }
  | { type: "delete"; user: User };

export default function SettingsUsersPage() {
  return (
    <Suspense fallback={null}>
      <SettingsUsersPageInner />
    </Suspense>
  );
}

function SettingsUsersPageInner() {
  const toast = useToast();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { canAccess, loading: permLoading } = usePermissions();
  const initialTab = (searchParams?.get("tab") as TabKey) || "users";
  const [activeTab, setActiveTab] = useState<TabKey>(
    (["users", "service-accounts", "webhooks", "api-activity"] as TabKey[]).includes(initialTab)
      ? initialTab
      : "users",
  );
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
  const [confirmBusy, setConfirmBusy] = useState(false);
  const [tempPassword, setTempPassword] = useState("");
  const [showTempPasswordForm, setShowTempPasswordForm] = useState(false);
  const [tempPasswordUser, setTempPasswordUser] = useState<User | null>(null);
  const [smtpConfigured, setSmtpConfigured] = useState(false);
  const [roles, setRoles] = useState<Role[]>([]);

  useEffect(() => {
    if (permLoading) return;
    if (!canAccess("users", "view")) { router.push("/dashboard"); return; }
    fetchUsers();
    api.get<RoleListResponse>("/api/roles")
      .then((data) => setRoles(data.roles))
      .catch((e) => {
        logError("loading roles", e);
        toast.error(loadFailureMessage("Roles"));
      });
    fetchNeverLoggedIn();
    api.get<{ setup_complete: boolean; smtp_configured: boolean }>("/api/bootstrap/status")
      .then((data) => setSmtpConfigured(data.smtp_configured))
      .catch((e) => {
        logError("loading the setup status", e);
        toast.error(loadFailureMessage("The setup status"));
      });
  }, [router, permLoading, canAccess]);

  const fetchUsers = async () => {
    try {
      const data = await api.get<{ users: User[]; total: number }>("/api/users");
      setUsers(data.users);
    } catch { /* handled */ } finally {
      setLoading(false);
    }
  };

  const fetchNeverLoggedIn = () => {
    api.get<{ users: NeverLoggedInUser[] }>("/api/access-logs/never-logged-in")
      .then((data) => setNeverLoggedIn(data.users))
      .catch((e) => {
        logError("loading the never-logged-in list", e);
        toast.error(loadFailureMessage("The never-signed-in list"));
      });
  };

  const clearMessages = () => { setError(""); setSuccess(""); };

  const handleConfirmAction = async () => {
    if (!pendingAction) return;
    clearMessages();
    setConfirmBusy(true);

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
        case "role_change": {
          const targetRole = roles.find((r) => r.name === pendingAction.newRole);
          if (!targetRole) { setError("Role not found"); break; }
          await api.patch(`/api/users/${pendingAction.user.id}`, { role_id: targetRole.id });
          setSuccess(`${pendingAction.user.email} role changed to ${pendingAction.newRole}`);
        }
          break;
        case "resend_invite":
          await api.post(`/api/users/${pendingAction.user.id}/resend-invite`);
          setSuccess(`Invitation resent to ${pendingAction.user.email}`);
          break;
        case "reset_password_email":
          await api.post(`/api/users/${pendingAction.user.id}/admin-reset-password`, { mode: "email" });
          setSuccess(`Password reset email sent to ${pendingAction.user.email}`);
          break;
        case "reactivate":
          await api.post(`/api/users/${pendingAction.user.id}/reactivate`);
          setSuccess(`${pendingAction.user.email} reactivated`);
          break;
        case "delete":
          await api.delete(`/api/users/${pendingAction.user.id}`);
          setSuccess(`${pendingAction.user.email} deleted`);
          break;
      }
      fetchUsers();
      fetchNeverLoggedIn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Action failed");
    } finally {
      setConfirmBusy(false);
      setPendingAction(null);
    }
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

    const updates: Record<string, unknown> = {};
    if (editName !== (editingUser.name || "")) updates.name = editName;
    const roleChanged = editRole !== editingUser.role_name;

    if (!roleChanged && Object.keys(updates).length === 0) {
      setEditingUser(null);
      return;
    }

    // If role is changing, require confirmation
    if (roleChanged) {
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
          message: `Change ${pendingAction.user.email} from ${pendingAction.user.role_name} to ${pendingAction.newRole}?`,
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
      case "reactivate":
        return {
          title: "Reactivate User",
          message: `Reactivate ${pendingAction.user.email}? They will regain access with their previous role.`,
          variant: "default",
        };
      case "delete":
        return {
          title: "Delete User",
          message: `Permanently delete ${pendingAction.user.email}? This cannot be undone.`,
          variant: "danger",
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
        <Button size="sm"
          onClick={() => {
            setEditingUser(user);
            setEditName(user.name || "");
            setEditRole(user.role_name);
            setViewingUser(null);
          }}>
          Edit
        </Button>
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
        <PasswordResetActions
          user={user}
          smtpConfigured={smtpConfigured}
          onSendResetEmail={(u) => {
            setPendingAction({ type: "reset_password_email", user: u });
            setViewingUser(null);
          }}
          onSetManualPassword={(u) => {
            setTempPasswordUser(u);
            setShowTempPasswordForm(true);
            setViewingUser(null);
          }}
        />
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
        {isDeactivated && (
          <button
            onClick={() => {
              setPendingAction({ type: "reactivate", user });
              setViewingUser(null);
            }}
            className="px-3 py-1.5 text-sm bg-white border border-green-300 text-green-700 rounded hover:bg-green-50"
          >
            Reactivate
          </button>
        )}
        {isDeactivated && !user.last_login && (
          <Button variant="danger" size="sm"
            onClick={() => {
              setPendingAction({ type: "delete", user });
              setViewingUser(null);
            }}>
            Delete
          </Button>
        )}
      </div>
    );
  };

  const confirm = getConfirmMessage();

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-bold">Users &amp; Accounts</h1>
          <p data-testid="page-description" className="text-sm text-gray-500 mt-1">
            People with access to this instance, plus pending invitations and service accounts.
          </p>
        </div>
        {activeTab === "users" && (
          <Button
            onClick={() => setShowInvite(!showInvite)}>
            {showInvite ? "Close" : "Invite Users"}
          </Button>
        )}
      </div>

      <div className="flex border-b border-gray-200 mb-6">
        {(Object.keys(TAB_LABELS) as TabKey[]).map((key) => (
          <button
            key={key}
            onClick={() => {
              setActiveTab(key);
              const params = new URLSearchParams(searchParams?.toString() ?? "");
              params.set("tab", key);
              router.replace(`/settings/users?${params.toString()}`);
            }}
            className={`px-4 py-2 -mb-px border-b-2 text-sm font-medium ${
              activeTab === key
                ? "border-bioaf-600 text-bioaf-700"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {TAB_LABELS[key]}
          </button>
        ))}
      </div>

      {activeTab === "service-accounts" && (
        <ServiceAccountsTab
          roles={roles}
          onRolesChanged={() => {
            api.get<RoleListResponse>("/api/roles")
              .then((data) => setRoles(data.roles))
              .catch((e) => {
                logError("refreshing roles", e);
                toast.error(loadFailureMessage("Roles"));
              });
          }}
        />
      )}
      {activeTab === "webhooks" && <WebhooksTab />}
      {activeTab === "api-activity" && <ApiActivityTab />}

      {activeTab === "users" && (
      <>
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
                {u.role_name && u.role_name !== "viewer" && (
                  <span className="ml-2 text-amber-500">({u.role_name})</span>
                )}
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
        <Card className="mb-6">
          <h2 className="text-lg font-semibold mb-4">Invite Users</h2>
          <InviteForm roles={roles} />
        </Card>
      )}

      {loading ? (
        <LoadingSpinner size="lg" />
      ) : (
        <Card padding="none" className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Email
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Name
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Role
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Status
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Last Login
                </th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Session Keys
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {users.map((user) => (
                <tr
                  key={user.id}
                  className="hover:bg-gray-50 cursor-pointer"
                  {...clickableRow(() => setViewingUser(user))}
                >
                  <td className="px-4 py-3 text-sm">{user.email}</td>
                  <td className="px-4 py-3 text-sm text-gray-500">
                    {user.name || NOT_SET}
                  </td>
                  <td className="px-4 py-3 text-sm">
                    <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-700">
                      {user.role_name}
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
                      <span className="text-green-700 text-sm" title="Session credentials configured">
                        &#10003;
                      </span>
                    ) : (
                      <span className="text-gray-500 text-sm">{NOT_SET}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      </>
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
                : pendingAction.type === "delete" ? "Delete"
                  : pendingAction.type === "reactivate" ? "Reactivate"
                    : "Confirm"
          }
          onConfirm={handleConfirmAction}
          onCancel={() => setPendingAction(null)}
          busy={confirmBusy}
        />
      )}

      {/* Change password form modal */}
      {showTempPasswordForm && tempPasswordUser && (
        <Modal
          open
          title="Change Password"
          onClose={() => setShowTempPasswordForm(false)}
          size="md"
          footer={
            <>
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
            <Button
              onClick={handleSetTempPassword}
              disabled={!tempPassword}>
              Set Password
            </Button>
            </>
          }
        >
          <p className="text-sm text-gray-600 mb-4">
            Set a new password for {tempPasswordUser.email}. They should change
            it after logging in.
          </p>
          <input aria-label="Enter new password"
            type="password"
            value={tempPassword}
            onChange={(e) => setTempPassword(e.target.value)}
            placeholder="Enter new password"
            className="w-full px-3 py-2 border rounded-md text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-bioaf-500"
          />
        </Modal>
      )}

      {/* Edit user modal */}
      {editingUser && (
        <Modal
          open
          title={`Edit ${editingUser.email}`}
          onClose={() => setEditingUser(null)}
          size="md"
          footer={
            <>
            <button
              onClick={() => setEditingUser(null)}
              className="px-4 py-2 text-sm text-gray-700 bg-gray-100 rounded hover:bg-gray-200"
            >
              Cancel
            </button>
            <Button
              onClick={handleEditSave}>
              Save Changes
            </Button>
            </>
          }
        >
          <div className="space-y-4">
            <div>
              <label htmlFor="name" className="block text-sm font-medium text-gray-700 mb-1">
                Name
              </label>
              <input id="name"
                type="text"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-bioaf-500"
              />
            </div>
            <div>
              <label htmlFor="role" className="block text-sm font-medium text-gray-700 mb-1">
                Role
              </label>
              <select id="role"
                value={editRole}
                onChange={(e) => setEditRole(e.target.value)}
                className="w-full px-3 py-2 border rounded-md text-sm"
              >
                {roles.map((r) => (
                  <option key={r.id} value={r.name}>{r.name}</option>
                ))}
              </select>
            </div>
          </div>
        </Modal>
      )}

      {/* Detail modal */}
      {viewingUser && (
        <DetailModal
          title={viewingUser.name || viewingUser.email}
          onClose={() => setViewingUser(null)}
          fields={[
            { label: "Email", value: viewingUser.email },
            { label: "Name", value: viewingUser.name },
            { label: "Role", value: viewingUser.role_name },
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
  );
}
