"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/Button";
import { NotificationItem } from "@/components/notifications/NotificationItem";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { ErrorState } from "@/components/shared/ErrorState";

interface Notification {
  id: number;
  event_type: string;
  title: string;
  message: string | null;
  severity: string;
  read: boolean;
  created_at: string;
  metadata_json?: Record<string, unknown> | null;
}

export default function NotificationsPage() {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Bumped by the error state's Retry: the load lives inside an effect, so this
  // is what re-triggers it.
  const [reloadKey, setReloadKey] = useState(0);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "unread">("all");
  const [severityFilter, setSeverityFilter] = useState("");

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        let url = `/api/notifications?page=${page}&page_size=20`;
        if (filter === "unread") url += "&unread=true";
        if (severityFilter) url += `&severity=${severityFilter}`;
        const data = await api.get<{ notifications: Notification[]; total: number }>(url);
        setNotifications(data.notifications);
        setTotal(data.total);
        setLoadError(null);
      } catch (e) {
        logError("loading notifications", e);
        setLoadError(loadFailureMessage("Notifications"));
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [page, filter, severityFilter, reloadKey]);

  const handleMarkRead = async (id: number) => {
    await api.patch(`/api/notifications/${id}/read`);
    setNotifications(notifications.map((n) => (n.id === id ? { ...n, read: true } : n)));
  };

  const handleDelete = async (id: number) => {
    await api.delete(`/api/notifications/${id}`);
    setNotifications(notifications.filter((n) => n.id !== id));
    setTotal(total - 1);
  };

  const handleMarkAllRead = async () => {
    await api.post("/api/notifications/mark-all-read");
    setNotifications(notifications.map((n) => ({ ...n, read: true })));
  };

  const totalPages = Math.ceil(total / 20);

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-ink">Notifications</h1>
        <div className="flex items-center gap-3">
          <Link
            href="/profile/notifications"
            className="text-sm text-bioaf-600 hover:text-bioaf-700"
          >
            Preferences
          </Link>
          <Button size="sm" onClick={handleMarkAllRead}>
            Mark all read
          </Button>
        </div>
      </div>

      <div className="flex gap-3 mb-4">
        <select aria-label="Filter notifications"
          value={filter}
          onChange={(e) => { setFilter(e.target.value as "all" | "unread"); setPage(1); }}
          className="border border-gray-300 rounded px-3 py-1.5 text-sm"
        >
          <option value="all">All</option>
          <option value="unread">Unread</option>
        </select>
        <select aria-label="Filter by severity"
          value={severityFilter}
          onChange={(e) => { setSeverityFilter(e.target.value); setPage(1); }}
          className="border border-gray-300 rounded px-3 py-1.5 text-sm"
        >
          <option value="">All severities</option>
          <option value="info">Info</option>
          <option value="warning">Warning</option>
          <option value="critical">Critical</option>
        </select>
      </div>

      <div className="bg-surface rounded-lg border border-hairline">
        {loading ? (
          <ContentLoading variant="table" />
        ) : loadError ? (
          <ErrorState message={loadError} onRetry={() => setReloadKey((k) => k + 1)} />
        ) : notifications.length === 0 ? (
          <div className="p-8 text-center text-ink-subtle">No notifications</div>
        ) : (
          notifications.map((n) => (
            <NotificationItem
              key={n.id}
              notification={n}
              onMarkRead={() => handleMarkRead(n.id)}
              showActions
              onDelete={() => handleDelete(n.id)}
            />
          ))
        )}
      </div>

      {totalPages > 1 && (
        <div className="flex justify-center gap-2 mt-4">
          <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            Previous
          </Button>
          <span className="px-3 py-1 text-sm text-gray-600">
            Page {page} of {totalPages}
          </span>
          <Button variant="secondary" size="sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
            Next
          </Button>
        </div>
      )}
    </main>
  );
}
