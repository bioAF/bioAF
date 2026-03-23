"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { isAuthenticated, getCurrentUser } from "@/lib/auth";
import { api, ApiError } from "@/lib/api";
import { ContentLoading } from "@/components/shared/ContentLoading";

interface SessionCredentialResponse {
  configured: boolean;
  username: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export default function ProfilePage() {
  const router = useRouter();
  const [currentUser, setCurrentUser] = useState<{
    email: string;
    role_name: string;
    name?: string;
  } | null>(null);

  // Password change state
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmNewPassword, setConfirmNewPassword] = useState("");
  const [pwSaving, setPwSaving] = useState(false);
  const [pwMessage, setPwMessage] = useState("");
  const [pwError, setPwError] = useState("");

  // Session credentials state
  const [credLoading, setCredLoading] = useState(true);
  const [credSaving, setCredSaving] = useState(false);
  const [cred, setCred] = useState<SessionCredentialResponse | null>(null);
  const [credUsername, setCredUsername] = useState("");
  const [credPassword, setCredPassword] = useState("");
  const [credConfirm, setCredConfirm] = useState("");
  const [credMessage, setCredMessage] = useState("");
  const [credError, setCredError] = useState("");
  const [credFormOpen, setCredFormOpen] = useState(false);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    const user = getCurrentUser();
    if (user) {
      setCurrentUser({
        email: String(user.email || ""),
        role_name: String(user.role_name || ""),
        name: user.name ? String(user.name) : undefined,
      });
    }
    loadCredentials();
  }, [router]);

  const loadCredentials = async () => {
    try {
      const data = await api.get<SessionCredentialResponse>(
        "/api/auth/me/session-credentials",
      );
      setCred(data);
      if (data.username) setCredUsername(data.username);
    } catch {
      // ignore
    } finally {
      setCredLoading(false);
    }
  };

  const handleChangePassword = async () => {
    setPwError("");
    setPwMessage("");

    if (!currentPassword || !newPassword) {
      setPwError("All fields are required");
      return;
    }
    if (newPassword !== confirmNewPassword) {
      setPwError("New passwords do not match");
      return;
    }
    if (newPassword.length < 8) {
      setPwError("New password must be at least 8 characters");
      return;
    }

    setPwSaving(true);
    try {
      await api.post("/api/auth/me/change-password", {
        current_password: currentPassword,
        new_password: newPassword,
      });
      setCurrentPassword("");
      setNewPassword("");
      setConfirmNewPassword("");
      setPwMessage("Password changed successfully");
    } catch (e) {
      setPwError(
        e instanceof ApiError ? e.message : "Failed to change password",
      );
    } finally {
      setPwSaving(false);
    }
  };

  const handleSaveCredentials = async () => {
    setCredError("");
    setCredMessage("");

    if (!credPassword) {
      setCredError("Password is required");
      return;
    }
    if (credPassword !== credConfirm) {
      setCredError("Passwords do not match");
      return;
    }

    setCredSaving(true);
    try {
      const body: Record<string, string> = { password: credPassword };
      if (credUsername) body.username = credUsername;

      const data = await api.put<SessionCredentialResponse>(
        "/api/auth/me/session-credentials",
        body,
      );
      setCred(data);
      setCredPassword("");
      setCredConfirm("");
      setCredFormOpen(false);
      setCredMessage(
        data.username
          ? `Session credentials saved. Your RStudio username is: ${data.username}`
          : "Session credentials saved",
      );
    } catch (e) {
      setCredError(
        e instanceof ApiError ? e.message : "Failed to save credentials",
      );
    } finally {
      setCredSaving(false);
    }
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold text-gray-900 mb-6">Profile</h1>

          {/* Account & Security */}
          {currentUser && (
            <div className="max-w-lg mb-8">
              <h2 className="text-lg font-semibold text-gray-900 mb-4">Account</h2>

              <div className="bg-white rounded-lg border border-gray-200 p-6 space-y-4">
                <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
                  <div>
                    <dt className="text-xs font-medium text-gray-500 uppercase">Email</dt>
                    <dd className="mt-0.5 text-gray-900">{currentUser.email}</dd>
                  </div>
                  <div>
                    <dt className="text-xs font-medium text-gray-500 uppercase">Role</dt>
                    <dd className="mt-0.5 text-gray-900">{currentUser.role_name}</dd>
                  </div>
                  {currentUser.name && (
                    <div>
                      <dt className="text-xs font-medium text-gray-500 uppercase">Name</dt>
                      <dd className="mt-0.5 text-gray-900">{currentUser.name}</dd>
                    </div>
                  )}
                </dl>

                <hr className="border-gray-200" />

                <h3 className="text-sm font-semibold text-gray-900">
                  Change Password
                </h3>

                {pwMessage && (
                  <div className="p-3 rounded bg-green-50 border border-green-200 text-green-700 text-sm">
                    {pwMessage}
                  </div>
                )}
                {pwError && (
                  <div className="p-3 rounded bg-red-50 border border-red-200 text-red-700 text-sm">
                    {pwError}
                  </div>
                )}

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Current password
                  </label>
                  <input
                    type="password"
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-bioaf-500"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    New password
                  </label>
                  <input
                    type="password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-bioaf-500"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Confirm new password
                  </label>
                  <input
                    type="password"
                    value={confirmNewPassword}
                    onChange={(e) => setConfirmNewPassword(e.target.value)}
                    className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-bioaf-500"
                  />
                </div>

                <button
                  onClick={handleChangePassword}
                  disabled={pwSaving}
                  className="w-full bg-bioaf-600 text-white px-4 py-2 rounded-md hover:bg-bioaf-700 disabled:opacity-50 text-sm font-medium"
                >
                  {pwSaving ? "Changing..." : "Change Password"}
                </button>
              </div>
            </div>
          )}

          {/* Divider */}
          <hr className="max-w-lg mb-8 border-gray-200" />

          {/* Session Credentials */}
          <div className="max-w-lg mb-8">
            <h2 className="text-lg font-semibold text-gray-900 mb-1">
              Session Credentials
            </h2>
            <p className="text-sm text-gray-500 mb-4">
              These credentials are used to log into RStudio sessions launched
              from bioAF. They are separate from your platform login.
            </p>

            {credMessage && (
              <div className="mb-4 p-3 rounded bg-green-50 border border-green-200 text-green-700 text-sm">
                {credMessage}
              </div>
            )}
            {credError && (
              <div className="mb-4 p-3 rounded bg-red-50 border border-red-200 text-red-700 text-sm">
                {credError}
              </div>
            )}

            {credLoading ? (
              <ContentLoading />
            ) : (
              <>
                {/* Status banner */}
                {cred?.configured ? (
                  <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg">
                    <div className="flex items-center justify-between">
                      <div>
                        <h3 className="text-sm font-semibold text-blue-800 mb-1">
                          Session credentials configured
                        </h3>
                        <p className="text-sm text-blue-700">
                          Username: <span className="font-mono font-bold">{cred.username}</span>
                        </p>
                        {cred.updated_at && (
                          <p className="text-xs text-blue-500 mt-1">
                            Last updated: {new Date(cred.updated_at).toLocaleString()}
                          </p>
                        )}
                      </div>
                      <button
                        onClick={() => setCredFormOpen(!credFormOpen)}
                        className="px-3 py-1.5 text-sm bg-blue-100 text-blue-700 rounded hover:bg-blue-200"
                      >
                        {credFormOpen ? "Cancel" : "Change"}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="p-4 bg-red-50 border border-red-200 rounded-lg">
                    <div className="flex items-center justify-between">
                      <div>
                        <h3 className="text-sm font-semibold text-red-800 mb-1">
                          No session credentials set
                        </h3>
                        <p className="text-sm text-red-700">
                          You need to set session credentials before launching RStudio sessions.
                        </p>
                      </div>
                      <button
                        onClick={() => setCredFormOpen(!credFormOpen)}
                        className="px-3 py-1.5 text-sm bg-red-100 text-red-700 rounded hover:bg-red-200"
                      >
                        {credFormOpen ? "Cancel" : "Set Up"}
                      </button>
                    </div>
                  </div>
                )}

                {/* Credential form (collapsed by default) */}
                {credFormOpen && (
                  <div className="mt-4 bg-white rounded-lg border border-gray-200 p-6 space-y-4">
                    <h3 className="text-sm font-semibold text-gray-900">
                      {cred?.configured
                        ? "Update credentials"
                        : "Set up session credentials"}
                    </h3>

                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Username (optional)
                      </label>
                      <input
                        type="text"
                        value={credUsername}
                        onChange={(e) => setCredUsername(e.target.value)}
                        placeholder="Auto-generated from your email"
                        className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-bioaf-500"
                      />
                      <p className="text-xs text-gray-400 mt-1">
                        Lowercase letters, numbers, and underscores. 3-32 characters.
                      </p>
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Password
                      </label>
                      <input
                        type="password"
                        value={credPassword}
                        onChange={(e) => setCredPassword(e.target.value)}
                        placeholder={cred?.configured ? "Enter new password" : "Choose a password"}
                        className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-bioaf-500"
                      />
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Confirm password
                      </label>
                      <input
                        type="password"
                        value={credConfirm}
                        onChange={(e) => setCredConfirm(e.target.value)}
                        placeholder="Confirm your password"
                        className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-bioaf-500"
                      />
                    </div>

                    <button
                      onClick={handleSaveCredentials}
                      disabled={credSaving}
                      className="w-full bg-bioaf-600 text-white px-4 py-2 rounded-md hover:bg-bioaf-700 disabled:opacity-50 text-sm font-medium"
                    >
                      {credSaving
                        ? "Saving..."
                        : cred?.configured
                          ? "Update Credentials"
                          : "Set Up Credentials"}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
