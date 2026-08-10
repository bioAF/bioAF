"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useWidgetData } from "@/hooks/useWidgetData";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { statusDotClass } from "@/lib/statusStyles";

interface ActivityEvent {
  id: number;
  event_type: string;
  summary: string;
  created_at: string;
  user_email?: string;
  severity?: string;
  entity_type?: string;
  entity_id?: number;
}

interface ActivityFeedWidgetProps {
  className?: string;
}

export function ActivityFeedWidget({ className }: ActivityFeedWidgetProps) {
  const { data, loading, error, retry } = useWidgetData(
    async () =>
      (await api.getWithRetry<{ events: ActivityEvent[] }>("/api/activity-feed?page_size=15"))
        .events,
    "The activity feed",
  );
  const events = data ?? [];

  function humanize(text: string): string {
    return text.replace(/[a-z0-9]+(_[a-z0-9]+)+/g, (match) =>
      match.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ")
    );
  }

  function formatTimeAgo(dateStr: string): string {
    const now = new Date();
    const date = new Date(dateStr);
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return "just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString();
  }

  return (
    <div
      className={`bg-white rounded-lg shadow p-5 flex flex-col ${className || ""}`}
      data-testid="widget-activity-feed"
    >
      <div className="flex items-center justify-between mb-3 shrink-0">
        <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide">
          Recent Activity
        </h3>
        <Link
          href="/activity"
          className="text-xs text-bioaf-600 hover:text-bioaf-700 hover:underline"
          data-testid="activity-expand-button"
        >
          View all
        </Link>
      </div>
      {loading && (
        <div className="flex items-center gap-2 text-gray-500 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" /><span className="text-sm">Loading activity...</span>
        </div>
      )}
      {error && !loading && (
        <div className="text-sm text-red-600" data-testid="widget-error">
          {error}
          <button onClick={retry} className="ml-2 text-bioaf-600 hover:underline">
            Retry
          </button>
        </div>
      )}
      {!loading && !error && events.length === 0 && (
        <p className="text-sm text-gray-500" data-testid="widget-empty">
          No recent activity. Events will appear here as you use the platform.
        </p>
      )}
      {!loading && !error && events.length > 0 && (
        <div className="flex-1 min-h-0 overflow-y-auto space-y-2">
          {events.map((e) => (
            <div key={e.id} className="flex items-start gap-2">
              <span
                className={`w-1.5 h-1.5 mt-1.5 rounded-full flex-shrink-0 ${statusDotClass(
                  "severity",
                  e.severity || "info",
                )}`}
              />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-gray-700 truncate">{humanize(e.summary)}</p>
                <p className="text-xs text-gray-500">
                  {e.user_email && (
                    <span className="text-gray-500">executed by {e.user_email} &middot; </span>
                  )}
                  {formatTimeAgo(e.created_at)}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
