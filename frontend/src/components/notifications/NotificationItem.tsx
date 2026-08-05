"use client";

import { useRouter } from "next/navigation";
import { notificationHref } from "@/lib/notificationLinks";

import { clickableCard } from "@/lib/a11y";

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
  notification: Notification;
  onMarkRead: () => void;
  showActions?: boolean;
  onDelete?: () => void;
  /** Called right before navigating, e.g. to close the dropdown. */
  onNavigate?: () => void;
}

const severityColors: Record<string, string> = {
  info: "bg-blue-100 text-blue-700",
  warning: "bg-yellow-100 text-yellow-700",
  critical: "bg-red-100 text-red-700",
};

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function NotificationItem({ notification, onMarkRead, showActions, onDelete, onNavigate }: Props) {
  const n = notification;
  const router = useRouter();
  const href = notificationHref(n);

  const handleClick = () => {
    if (!n.read) onMarkRead();
    if (href) {
      onNavigate?.();
      router.push(href);
    }
  };

  return (
    <div
      className={`px-4 py-3 border-b border-gray-50 hover:bg-gray-50 cursor-pointer ${
        !n.read ? "bg-blue-50/50" : ""
      }`}
      {...clickableCard(handleClick)}
    >
      <div className="flex items-start gap-3">
        <span
          className={`mt-0.5 text-xs px-1.5 py-0.5 rounded font-medium ${
            severityColors[n.severity] || severityColors.info
          }`}
        >
          {n.severity}
        </span>
        <div className="flex-1 min-w-0">
          <p className={`text-sm ${!n.read ? "font-semibold" : ""} text-gray-900 truncate`}>
            {n.title}
          </p>
          {n.message && (
            <p className="text-xs text-gray-500 mt-0.5 truncate">{n.message}</p>
          )}
          <p className="text-xs text-gray-500 mt-1">{timeAgo(n.created_at)}</p>
        </div>
        {showActions && onDelete && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
            className="text-gray-500 hover:text-red-500 text-xs"
          >
            Delete
          </button>
        )}
      </div>
    </div>
  );
}
