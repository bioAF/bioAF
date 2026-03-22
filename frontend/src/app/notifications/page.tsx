"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { NotificationItem } from "@/components/notifications/NotificationItem";
import { isAuthenticated } from "@/lib/auth";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { api } from "@/lib/api";

interface Notification {
  id: number;
  event_type: string;
  title: string;
  message: string | null;
  severity: string;
  read: boolean;
  created_at: string;
}

export default function NotificationsPage() {
  const router = useRouter();
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "unread">("all");
  const [severityFilter, setSeverityFilter] = useState("");

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
    }
  }, [router]);

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
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [page, filter, severityFilter]);

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
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-2xl font-bold text-gray-900">Notifications</h1>
            <div className="flex items-center gap-3">
              <Link
                href="/profile/notifications"
                className="text-sm text-bioaf-600 hover:text-bioaf-700"
              >
                Preferences
              </Link>
              <button
                onClick={handleMarkAllRead}
                className="text-sm bg-bioaf-600 text-white px-3 py-1.5 rounded hover:bg-bioaf-700"
              >
                Mark all read
              </button>
            </div>
          </div>

          <div className="flex gap-3 mb-4">
            <select
              value={filter}
              onChange={(e) => { setFilter(e.target.value as "all" | "unread"); setPage(1); }}
              className="border border-gray-300 rounded px-3 py-1.5 text-sm"
            >
              <option value="all">All</option>
              <option value="unread">Unread</option>
            </select>
            <select
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

          <div className="bg-white rounded-lg border border-gray-200">
            {loading ? (
              <ContentLoading />
            ) : notifications.length === 0 ? (
              <div className="p-8 text-center text-gray-500">No notifications</div>
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
              <button
                disabled={page <= 1}
                onClick={() => setPage(page - 1)}
                className="px-3 py-1 border rounded text-sm disabled:opacity-50"
              >
                Previous
              </button>
              <span className="px-3 py-1 text-sm text-gray-600">
                Page {page} of {totalPages}
              </span>
              <button
                disabled={page >= totalPages}
                onClick={() => setPage(page + 1)}
                className="px-3 py-1 border rounded text-sm disabled:opacity-50"
              >
                Next
              </button>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
