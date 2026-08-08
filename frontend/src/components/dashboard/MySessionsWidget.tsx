"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useWidgetData } from "@/hooks/useWidgetData";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

interface RawSession {
  id: number;
  session_type: string;
  status: string;
  proxy_url?: string | null;
  access_url?: string | null;
}

interface SessionList {
  sessions: RawSession[];
}

interface CombinedSession {
  key: string;
  kind: string;
  sessionType: string;
  status: string;
  url: string | null;
  listHref: string;
}

const ACTIVE_STATUSES = new Set(["running", "idle", "active", "starting", "ready"]);

export function MySessionsWidget() {
  const { data: items, loading, error, retry } = useWidgetData(
    async () => {
      // No per-source fallback: an empty list stood in for a failed fetch, so a
      // total outage rendered "No active sessions." -- and these sessions bill by
      // the hour, which makes a false "you have none" the expensive direction to
      // be wrong in.
      const [notebooks, workNodes] = await Promise.all([
        api.getWithRetry<SessionList>("/api/v1/notebooks/sessions"),
        api.getWithRetry<SessionList>("/api/v1/work-nodes/sessions"),
      ]);
      const combined: CombinedSession[] = [
        ...(notebooks.sessions || []).map((s) => ({
          key: `nb-${s.id}`,
          kind: "Notebook",
          sessionType: s.session_type,
          status: s.status,
          url: s.proxy_url || s.access_url || null,
          listHref: "/notebooks",
        })),
        ...(workNodes.sessions || []).map((s) => ({
          key: `wn-${s.id}`,
          kind: "Work node",
          sessionType: s.session_type,
          status: s.status,
          url: s.access_url || s.proxy_url || null,
          listHref: "/workbench/work-nodes",
        })),
      ].filter((s) => ACTIVE_STATUSES.has(s.status));
      return combined;
    },
    "Your sessions",
  );

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-my-sessions">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        My active sessions
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-500 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" />
          <span className="text-sm">Loading sessions...</span>
        </div>
      )}
      {error && !loading && (
        <div className="text-sm text-red-600" data-testid="widget-error">
          {error}
          <button
            onClick={retry}
            className="ml-2 text-bioaf-600 hover:underline"
          >
            Retry
          </button>
        </div>
      )}
      {!loading && !error && items && items.length === 0 && (
        <p className="text-sm text-gray-500" data-testid="widget-empty">
          No active sessions.
        </p>
      )}
      {!loading && !error && items && items.length > 0 && (
        <ul className="space-y-1">
          {items.map((s) => {
            const label = (
              <span className="flex items-center justify-between gap-2 w-full">
                <span className="truncate text-sm text-gray-800">
                  {s.kind}: {s.sessionType}
                </span>
                <span className="shrink-0 rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
                  {s.status}
                </span>
              </span>
            );
            return (
              <li key={s.key}>
                <Link
                  href={s.url || s.listHref}
                  className="flex items-center rounded px-1 py-0.5 hover:bg-gray-50"
                >
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
