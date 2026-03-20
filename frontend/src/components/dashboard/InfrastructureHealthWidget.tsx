"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

interface ComponentHealth {
  name: string;
  status: string;
  enabled: boolean;
}

export function InfrastructureHealthWidget() {
  const [components, setComponents] = useState<ComponentHealth[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timeout = setTimeout(() => setLoading(false), 30000);
    api
      .getWithRetry<{ components: ComponentHealth[] }>("/api/components")
      .then((data) => setComponents(data.components.filter((c) => c.enabled)))
      .catch(() => setError("Failed to load component health"))
      .finally(() => { clearTimeout(timeout); setLoading(false); });
    return () => clearTimeout(timeout);
  }, []);

  const statusColor: Record<string, string> = {
    healthy: "bg-green-400",
    degraded: "bg-yellow-400",
    unhealthy: "bg-red-400",
    unknown: "bg-gray-400",
  };

  return (
    <div className="bg-white rounded-lg shadow p-5" data-testid="widget-infrastructure-health">
      <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
        Infrastructure Health
      </h3>
      {loading && (
        <div className="flex items-center gap-2 text-gray-400 py-4" data-testid="widget-loading">
          <LoadingSpinner size="sm" /><span className="text-sm">Loading health...</span>
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
      {!loading && !error && components.length === 0 && (
        <p className="text-sm text-gray-400" data-testid="widget-empty">
          No enabled components found. Enable components in Infrastructure settings.
        </p>
      )}
      {!loading && !error && components.length > 0 && (
        <div className="grid grid-cols-2 gap-2">
          {components.map((c) => (
            <div key={c.name} className="flex items-center gap-2 px-2 py-1.5 rounded bg-gray-50">
              <span className={`w-2 h-2 rounded-full ${statusColor[c.status] || statusColor.unknown}`} />
              <span className="text-sm text-gray-700 truncate">{c.name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
