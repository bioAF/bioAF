"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

interface User {
  id: number;
  email: string;
  name: string | null;
  status: string;
}

interface UserList {
  users: User[];
}

export function PendingInvitesWidget() {
  const [invited, setInvited] = useState<User[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timeout = setTimeout(() => setLoading(false), 60000);
    api
      .getWithRetry<UserList>("/api/users")
      .then((res) => setInvited((res.users || []).filter((u) => u.status === "invited")))
      .catch(() => setError("Failed to load users"))
      .finally(() => {
        clearTimeout(timeout);
        setLoading(false);
      });
    return () => clearTimeout(timeout);
  }, []);

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-pending-invites">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Pending invites
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-500 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" />
          <span className="text-sm">Loading invites...</span>
        </div>
      )}
      {error && !loading && (
        <div className="text-sm text-red-600" data-testid="widget-error">
          {error}
          <button
            onClick={() => window.location.reload()}
            className="ml-2 text-bioaf-600 hover:underline"
          >
            Retry
          </button>
        </div>
      )}
      {!loading && !error && invited && invited.length === 0 && (
        <p className="text-sm text-gray-500" data-testid="widget-empty">
          No pending invites.
        </p>
      )}
      {!loading && !error && invited && invited.length > 0 && (
        <div>
          <div className="text-3xl font-bold text-bioaf-600">{invited.length}</div>
          <ul className="mt-2 space-y-1">
            {invited.slice(0, 5).map((u) => (
              <li key={u.id} className="truncate text-sm text-gray-700">
                {u.email}
              </li>
            ))}
          </ul>
          <Link
            href="/settings/users"
            className="text-xs text-bioaf-600 hover:underline mt-2 inline-block"
          >
            Manage users
          </Link>
        </div>
      )}
    </div>
  );
}
