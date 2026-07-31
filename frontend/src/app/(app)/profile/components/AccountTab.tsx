"use client";

import { useEffect, useState } from "react";
import { getCurrentUser, setToken } from "@/lib/auth";
import { api, ApiError } from "@/lib/api";

export function AccountTab() {
  const [email, setEmail] = useState("");
  const [roleName, setRoleName] = useState("");

  // Name editing
  const [name, setName] = useState("");
  const [nameSaving, setNameSaving] = useState(false);
  const [nameMessage, setNameMessage] = useState("");
  const [nameError, setNameError] = useState("");

  // Password change
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmNewPassword, setConfirmNewPassword] = useState("");
  const [pwSaving, setPwSaving] = useState(false);
  const [pwMessage, setPwMessage] = useState("");
  const [pwError, setPwError] = useState("");

  useEffect(() => {
    const user = getCurrentUser();
    if (user) {
      setEmail(String(user.email || ""));
      setRoleName(String(user.role_name || ""));
      setName(user.name ? String(user.name) : "");
    }
  }, []);

  const handleSaveName = async () => {
    setNameError("");
    setNameMessage("");
    if (!name.trim()) {
      setNameError("Name cannot be empty");
      return;
    }
    setNameSaving(true);
    try {
      await api.patch("/api/auth/me", { name: name.trim() });
      // Re-issue the token so the new name is reflected in the header and
      // anywhere else that reads it from the JWT.
      const res = await api.post<{ access_token: string }>("/api/auth/refresh");
      setToken(res.access_token);
      window.dispatchEvent(new Event("profile-updated"));
      setNameMessage("Name updated");
    } catch (e) {
      setNameError(e instanceof ApiError ? e.message : "Failed to update name");
    } finally {
      setNameSaving(false);
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
      setPwError(e instanceof ApiError ? e.message : "Failed to change password");
    } finally {
      setPwSaving(false);
    }
  };

  return (
    <div className="max-w-lg space-y-8">
      <div>
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Account</h2>
        <div className="bg-white rounded-lg border border-gray-200 p-6 space-y-4">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
            <div>
              <dt className="text-xs font-medium text-gray-500 uppercase">Email</dt>
              <dd className="mt-0.5 text-gray-900">{email}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-gray-500 uppercase">Role</dt>
              <dd className="mt-0.5 text-gray-900">{roleName}</dd>
            </div>
          </dl>

          <hr className="border-gray-200" />

          <h3 className="text-sm font-semibold text-gray-900">Name</h3>
          {nameMessage && (
            <div className="p-3 rounded bg-green-50 border border-green-200 text-green-700 text-sm">
              {nameMessage}
            </div>
          )}
          {nameError && (
            <div className="p-3 rounded bg-red-50 border border-red-200 text-red-700 text-sm">
              {nameError}
            </div>
          )}
          <div>
            <label htmlFor="account-name" className="block text-sm font-medium text-gray-700 mb-1">
              Display name
            </label>
            <input
              id="account-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Your name"
              className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-bioaf-500"
            />
          </div>
          <button
            onClick={handleSaveName}
            disabled={nameSaving}
            className="bg-bioaf-600 text-white px-4 py-2 rounded-md hover:bg-bioaf-700 disabled:opacity-50 text-sm font-medium"
          >
            {nameSaving ? "Saving..." : "Save Name"}
          </button>
        </div>
      </div>

      <div>
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Change Password</h2>
        <div className="bg-white rounded-lg border border-gray-200 p-6 space-y-4">
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
            <label htmlFor="account-current-password" className="block text-sm font-medium text-gray-700 mb-1">
              Current password
            </label>
            <input
              id="account-current-password"
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-bioaf-500"
            />
          </div>

          <div>
            <label htmlFor="account-new-password" className="block text-sm font-medium text-gray-700 mb-1">
              New password
            </label>
            <input
              id="account-new-password"
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-bioaf-500"
            />
          </div>

          <div>
            <label htmlFor="account-confirm-password" className="block text-sm font-medium text-gray-700 mb-1">
              Confirm new password
            </label>
            <input
              id="account-confirm-password"
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
    </div>
  );
}
