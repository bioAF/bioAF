"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { NotificationItem } from "./NotificationItem";

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

interface Props {
  onClose: () => void;
  onCountChange: (count: number) => void;
}

export function NotificationDropdown({ onClose, onCountChange }: Props) {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const data = await api.get<{ notifications: Notification[] }>(
          "/api/notifications?page_size=10"
        );
        setNotifications(data.notifications);
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  const handleMarkAllRead = async () => {
    try {
      await api.post("/api/notifications/mark-all-read");
      setNotifications(notifications.map((n) => ({ ...n, read: true })));
      onCountChange(0);
    } catch {
      // ignore
    }
  };

  const handleMarkRead = async (id: number) => {
    try {
      await api.patch(`/api/notifications/${id}/read`);
      setNotifications(
        notifications.map((n) => (n.id === id ? { ...n, read: true } : n))
      );
      onCountChange(Math.max(0, notifications.filter((n) => !n.read).length - 1));
    } catch {
      // ignore
    }
  };

  return (
    <div className="absolute right-0 mt-2 w-96 bg-white rounded-lg shadow-lg border border-gray-200 z-50">
      <div className="flex items-center justify-between p-4 border-b border-gray-100">
        <h3 className="font-semibold text-gray-900">Notifications</h3>
        <button
          onClick={handleMarkAllRead}
          className="text-xs text-bioaf-600 hover:text-bioaf-700"
        >
          Mark all read
        </button>
      </div>

      <div className="max-h-96 overflow-y-auto">
        {loading ? (
          <div className="p-4 text-center text-gray-500 text-sm">Loading...</div>
        ) : notifications.length === 0 ? (
          <div className="p-4 text-center text-gray-500 text-sm">No notifications</div>
        ) : (
          notifications.map((n) => (
            <NotificationItem
              key={n.id}
              notification={n}
              onMarkRead={() => handleMarkRead(n.id)}
              onNavigate={onClose}
            />
          ))
        )}
      </div>

      <div className="p-3 border-t border-gray-100 text-center">
        <Link
          href="/notifications"
          onClick={onClose}
          className="text-sm text-bioaf-600 hover:text-bioaf-700"
        >
          View all notifications
        </Link>
      </div>
    </div>
  );
}
