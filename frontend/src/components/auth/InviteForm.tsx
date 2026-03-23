"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { Role } from "@/lib/types";

interface InviteFormProps {
  roles?: Role[];
}

export function InviteForm({ roles = [] }: InviteFormProps) {
  const [email, setEmail] = useState("");
  const [selectedRoleName, setSelectedRoleName] = useState("");
  const [bulkEmails, setBulkEmails] = useState("");
  const [results, setResults] = useState<Array<{ email: string; status: string }>>([]);
  const [error, setError] = useState("");

  // Default to first non-admin role, or first role
  const defaultRole = roles.find((r) => r.name !== "admin") || roles[0];
  const activeRole = selectedRoleName || defaultRole?.name || "";
  const activeRoleId = roles.find((r) => r.name === activeRole)?.id;

  const handleInvite = async () => {
    setError("");
    if (!activeRoleId) { setError("No role selected"); return; }
    try {
      await api.post("/api/users", { email, role_id: activeRoleId });
      setResults((prev) => [...prev, { email, status: "invited" }]);
      setEmail("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to invite user");
    }
  };

  const handleBulkInvite = async () => {
    setError("");
    if (!activeRoleId) { setError("No role selected"); return; }
    const emails = bulkEmails
      .split(/[\n,]/)
      .map((e) => e.trim())
      .filter((e) => e);

    if (emails.length === 0) return;

    try {
      const response = await api.post<{
        results: Array<{ email: string; status: string }>;
      }>("/api/users/bulk-invite", {
        invites: emails.map((e) => ({ email: e, role_id: activeRoleId })),
      });
      setResults((prev) => [...prev, ...response.results]);
      setBulkEmails("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to bulk invite");
    }
  };

  return (
    <div className="space-y-4">
      {error && (
        <div className="p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm">
          {error}
        </div>
      )}

      <div className="flex gap-2">
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email address"
          className="flex-1 px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500"
        />
        <select
          value={activeRole}
          onChange={(e) => setSelectedRoleName(e.target.value)}
          className="px-3 py-2 border rounded"
        >
          {roles.map((r) => (
            <option key={r.id} value={r.name}>{r.name}</option>
          ))}
        </select>
        <button
          onClick={handleInvite}
          className="px-4 py-2 bg-bioaf-600 text-white rounded hover:bg-bioaf-700"
        >
          Invite
        </button>
      </div>

      <div>
        <label htmlFor="bulk-invite-emails" className="block text-sm font-medium text-gray-700 mb-1">
          Bulk invite (one email per line or comma-separated)
        </label>
        <textarea
          id="bulk-invite-emails"
          value={bulkEmails}
          onChange={(e) => setBulkEmails(e.target.value)}
          rows={3}
          className="w-full px-3 py-2 border rounded focus:ring-2 focus:ring-bioaf-500"
          placeholder="user1@example.com&#10;user2@example.com"
        />
        <button
          onClick={handleBulkInvite}
          className="mt-2 px-4 py-2 bg-gray-600 text-white rounded hover:bg-gray-700 text-sm"
        >
          Bulk Invite
        </button>
      </div>

      {results.length > 0 && (
        <div className="text-sm">
          <h4 className="font-medium mb-1">Invited:</h4>
          {results.map((r, i) => (
            <p key={i} className="text-gray-600">
              {r.email} — {r.status}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
