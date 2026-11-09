"use client";

import { useCallback } from "react";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { useResource } from "@/hooks/useResource";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { statusDotClass } from "@/lib/statusStyles";

interface ServiceHealth {
  name: string;
  status: string;
}

const SERVICE_LABELS: Record<string, string> = {
  auth: "Authentication",
  backups: "Backups",
  environments: "Environments",
  experiments: "Experiments",
  infrastructure: "Infrastructure",
  notebooks: "Notebooks",
  notifications: "Notifications",
  pipelines: "Pipelines",
  projects: "Projects",
  samples: "Samples",
  storage: "Storage",
};

export function InfrastructureHealthWidget() {
  // This widget keeps its own loader rather than moving to `useWidgetData` because it also
  // polls on a 60s interval. Each read starts after the previous one settles.
  const health = useResource(
    "service-health",
    useCallback(
      (signal: AbortSignal) => api.get<{ services: ServiceHealth[] }>("/api/health/services", { signal }),
      [],
    ),
    {
      poll: 60000,
      // Was `setError("Failed to load service health")`: a hand-written sentence
      // where thirteen sibling widgets share one, and it discarded the error
      // instead of logging it.
      onError: (err) => logError("loading service health on the dashboard", err),
    },
  );
  const services = health.data?.services ?? [];
  const loading = health.loading;
  // An initial failure replaces the content; a failed refresh keeps the last health, marked stale.
  const error = health.error !== null && !health.stale ? loadFailureMessage("Service health") : null;
  const loadHealth = health.refresh;

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-infrastructure-health">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Service Health
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-500 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" /><span className="text-sm">Loading health...</span>
        </div>
      )}
      {error && !loading && (
        <div className="text-sm text-red-600" data-testid="widget-error">
          {error}
          <button
            onClick={loadHealth}
            className="ml-2 text-bioaf-600 hover:underline"
          >
            Retry
          </button>
        </div>
      )}
      {health.stale && (
        <div className="text-sm text-red-600 mb-2" data-testid="widget-stale">
          Service health could not be refreshed. What is shown may be out of date.
          <button
            onClick={loadHealth}
            className="ml-2 text-bioaf-600 hover:underline"
          >
            Retry
          </button>
        </div>
      )}
      {!loading && !error && services.length === 0 && (
        <p className="text-sm text-gray-500" data-testid="widget-empty">
          No service activity in the last 5 minutes.
        </p>
      )}
      {!loading && !error && services.length > 0 && (
        <div className="grid grid-cols-2 gap-2">
          {services.map((s) => (
            <div key={s.name} className="flex items-center gap-2 px-2 py-1.5 rounded bg-gray-50">
              <span className={`w-2 h-2 rounded-full ${statusDotClass("serviceHealth", s.status)}`} />
              <span className="text-sm text-gray-700 truncate">{SERVICE_LABELS[s.name] || s.name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
