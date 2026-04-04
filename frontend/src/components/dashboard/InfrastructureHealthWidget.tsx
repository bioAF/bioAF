"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";

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
  const [services, setServices] = useState<ServiceHealth[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadHealth = () => {
    api
      .get<{ services: ServiceHealth[] }>("/api/health/services")
      .then((data) => {
        setServices(data.services);
        setError(null);
      })
      .catch(() => setError("Failed to load service health"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadHealth();
    const interval = setInterval(loadHealth, 60000);
    return () => clearInterval(interval);
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
        Service Health
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
            onClick={loadHealth}
            className="ml-2 text-bioaf-600 hover:underline"
          >
            Retry
          </button>
        </div>
      )}
      {!loading && !error && services.length === 0 && (
        <p className="text-sm text-gray-400" data-testid="widget-empty">
          No service activity in the last 5 minutes.
        </p>
      )}
      {!loading && !error && services.length > 0 && (
        <div className="grid grid-cols-2 gap-2">
          {services.map((s) => (
            <div key={s.name} className="flex items-center gap-2 px-2 py-1.5 rounded bg-gray-50">
              <span className={`w-2 h-2 rounded-full ${statusColor[s.status] || statusColor.unknown}`} />
              <span className="text-sm text-gray-700 truncate">{SERVICE_LABELS[s.name] || s.name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
