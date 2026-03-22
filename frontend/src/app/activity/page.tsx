"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { Breadcrumb } from "@/components/layout/Breadcrumb";
import { isAuthenticated } from "@/lib/auth";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { api } from "@/lib/api";

interface ActivityEvent {
  id: number;
  user_id: number | null;
  user_email?: string;
  event_type: string;
  entity_type: string | null;
  entity_id: number | null;
  summary: string;
  severity?: string;
  created_at: string;
}

const severityColors: Record<string, string> = {
  info: "bg-blue-100 text-blue-700",
  warning: "bg-amber-100 text-amber-700",
  critical: "bg-red-100 text-red-700",
};

const entityLinks: Record<string, (id: number) => string> = {
  experiment: (id) => `/experiments/${id}`,
  pipeline_run: (id) => `/pipelines/runs/${id}`,
  project: (id) => `/projects/${id}`,
  component: (id) => `/infrastructure/components/${id}`,
  reference_dataset: (id) => `/data/references/${id}`,
};

export default function ActivityFeedPage() {
  const router = useRouter();
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [userFilter, setUserFilter] = useState("");
  const [severityFilter, setSeverityFilter] = useState<string[]>([]);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
    }
  }, [router]);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const params = new URLSearchParams({ page: String(page), page_size: "50" });
        if (eventTypeFilter) params.set("event_type", eventTypeFilter);
        if (userFilter) params.set("user_email", userFilter);
        if (dateFrom) params.set("date_from", dateFrom);
        if (dateTo) params.set("date_to", dateTo);
        if (severityFilter.length > 0) params.set("severity", severityFilter.join(","));

        const data = await api.get<{ events: ActivityEvent[]; total: number }>(
          `/api/activity-feed?${params.toString()}`,
        );
        setEvents(data.events);
        setTotal(data.total);
      } catch {
        // handled by api client
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [page, eventTypeFilter, userFilter, dateFrom, dateTo, severityFilter]);

  const totalPages = Math.ceil(total / 50);

  const resetFilters = () => {
    setEventTypeFilter("");
    setUserFilter("");
    setSeverityFilter([]);
    setDateFrom("");
    setDateTo("");
    setPage(1);
  };

  const toggleSeverity = (sev: string) => {
    setSeverityFilter((prev) =>
      prev.includes(sev) ? prev.filter((s) => s !== sev) : [...prev, sev],
    );
    setPage(1);
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <Breadcrumb />
        <main className="flex-1 overflow-y-auto p-6" data-testid="activity-feed-page">
          <h1 className="text-2xl font-bold text-gray-900 mb-6">Activity Feed</h1>

          {/* Filters */}
          <div className="flex flex-wrap gap-3 mb-4 items-end" data-testid="activity-filters">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Event Type</label>
              <select
                value={eventTypeFilter}
                onChange={(e) => { setEventTypeFilter(e.target.value); setPage(1); }}
                className="border border-gray-300 rounded px-3 py-1.5 text-sm"
                data-testid="filter-event-type"
              >
                <option value="">All events</option>
                <option value="pipeline.completed">Pipeline Completed</option>
                <option value="pipeline.failed">Pipeline Failed</option>
                <option value="experiment.status_changed">Experiment Changed</option>
                <option value="data.uploaded">Data Uploaded</option>
                <option value="backup.failure">Backup Failure</option>
                <option value="files.cataloged">Files Cataloged</option>
                <option value="auto_run.submitted">Auto Run Submitted</option>
                <option value="budget.threshold_80">Budget 80%</option>
                <option value="budget.threshold_100">Budget 100%</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">User</label>
              <input
                type="text"
                placeholder="Filter by email"
                value={userFilter}
                onChange={(e) => { setUserFilter(e.target.value); setPage(1); }}
                className="border border-gray-300 rounded px-3 py-1.5 text-sm w-48"
                data-testid="filter-user"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">From</label>
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => { setDateFrom(e.target.value); setPage(1); }}
                className="border border-gray-300 rounded px-3 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">To</label>
              <input
                type="date"
                value={dateTo}
                onChange={(e) => { setDateTo(e.target.value); setPage(1); }}
                className="border border-gray-300 rounded px-3 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Severity</label>
              <div className="flex gap-1">
                {["info", "warning", "critical"].map((sev) => (
                  <button
                    key={sev}
                    onClick={() => toggleSeverity(sev)}
                    className={`px-2 py-1 rounded text-xs font-medium border ${
                      severityFilter.includes(sev)
                        ? severityColors[sev] + " border-transparent"
                        : "bg-white text-gray-500 border-gray-300"
                    }`}
                    data-testid={`filter-severity-${sev}`}
                  >
                    {sev}
                  </button>
                ))}
              </div>
            </div>
            {(eventTypeFilter || userFilter || dateFrom || dateTo || severityFilter.length > 0) && (
              <button
                onClick={resetFilters}
                className="text-xs text-gray-500 hover:text-gray-700 underline pb-1"
              >
                Clear filters
              </button>
            )}
          </div>

          {/* Events List */}
          <div className="bg-white rounded-lg border border-gray-200">
            {loading ? (
              <ContentLoading />
            ) : events.length === 0 ? (
              <div className="p-8 text-center text-gray-500" data-testid="activity-empty">
                No activity matches your filters.
              </div>
            ) : (
              <div className="divide-y divide-gray-100">
                {events.map((event) => (
                  <div key={event.id} className="px-4 py-3 hover:bg-gray-50" data-testid="activity-event">
                    <div className="flex items-start gap-3">
                      <div className="w-2 h-2 mt-2 rounded-full bg-bioaf-500 flex-shrink-0" />
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <p className="text-sm text-gray-900">{event.summary}</p>
                          {event.severity && (
                            <span
                              className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                                severityColors[event.severity] || "bg-gray-100 text-gray-700"
                              }`}
                            >
                              {event.severity}
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-3 mt-1">
                          {event.user_email && (
                            <span className="text-xs text-gray-500">{event.user_email}</span>
                          )}
                          <span className="text-xs font-mono text-gray-400">{event.event_type}</span>
                          {event.entity_type && event.entity_id && (
                            entityLinks[event.entity_type] ? (
                              <Link
                                href={entityLinks[event.entity_type](event.entity_id)}
                                className="text-xs text-bioaf-600 hover:underline"
                                data-testid="entity-link"
                              >
                                {event.entity_type} #{event.entity_id}
                              </Link>
                            ) : (
                              <span className="text-xs text-gray-400">
                                {event.entity_type} #{event.entity_id}
                              </span>
                            )
                          )}
                          <span className="text-xs text-gray-400">
                            {new Date(event.created_at).toLocaleString()}
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex justify-center gap-2 mt-4" data-testid="activity-pagination">
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
