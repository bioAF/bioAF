"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/Button";
import { Breadcrumb } from "@/components/layout/Breadcrumb";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { ErrorState } from "@/components/shared/ErrorState";
import { statusBadgeClass } from "@/lib/statusStyles";

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

const entityLinks: Record<string, (id: number) => string> = {
  experiment: (id) => `/experiments/${id}`,
  pipeline_run: (id) => `/pipelines/runs/${id}`,
  project: (id) => `/projects/${id}`,
  // Components are managed on the single Components screen (enable/disable toggles);
  // there is no per-component detail page.
  component: () => `/infrastructure/components`,
  reference_dataset: (id) => `/data/references/${id}`,
};

export default function ActivityFeedPage() {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Bumped by the error state's Retry: the load lives inside an effect, so this
  // is what re-triggers it.
  const [reloadKey, setReloadKey] = useState(0);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [userFilter, setUserFilter] = useState("");
  const [severityFilter, setSeverityFilter] = useState<string[]>([]);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

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
        setLoadError(null);
      } catch (e) {
        // The old comment claimed the api client handled this. It does not:
        // lib/api.ts only throws. Falling through left the page saying there was
        // no activity, which is a different claim from "we could not load it".
        logError("loading the activity feed", e);
        setLoadError(loadFailureMessage("Activity"));
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [page, eventTypeFilter, userFilter, dateFrom, dateTo, severityFilter, reloadKey]);

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
    <>
      <Breadcrumb />
      <main className="flex-1 overflow-y-auto p-6" data-testid="activity-feed-page">
        <h1 className="text-2xl font-bold text-ink mb-6">Activity Feed</h1>

        {/* Filters */}
        <div className="flex flex-wrap gap-3 mb-4 items-end" data-testid="activity-filters">
          <div>
            <label htmlFor="event-type" className="block text-xs text-ink-subtle mb-1">Event Type</label>
            <select id="event-type"
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
            <label htmlFor="user" className="block text-xs text-ink-subtle mb-1">User</label>
            <input id="user"
              type="text"
              placeholder="Filter by email"
              value={userFilter}
              onChange={(e) => { setUserFilter(e.target.value); setPage(1); }}
              className="border border-gray-300 rounded px-3 py-1.5 text-sm w-48"
              data-testid="filter-user"
            />
          </div>
          <div>
            <label htmlFor="from" className="block text-xs text-ink-subtle mb-1">From</label>
            <input id="from"
              type="date"
              value={dateFrom}
              onChange={(e) => { setDateFrom(e.target.value); setPage(1); }}
              className="border border-gray-300 rounded px-3 py-1.5 text-sm"
            />
          </div>
          <div>
            <label htmlFor="to" className="block text-xs text-ink-subtle mb-1">To</label>
            <input id="to"
              type="date"
              value={dateTo}
              onChange={(e) => { setDateTo(e.target.value); setPage(1); }}
              className="border border-gray-300 rounded px-3 py-1.5 text-sm"
            />
          </div>
          <div>
            <label className="block text-xs text-ink-subtle mb-1">Severity</label>
            <div className="flex gap-1">
              {["info", "warning", "critical"].map((sev) => (
                <button
                  key={sev}
                  onClick={() => toggleSeverity(sev)}
                  className={`px-2 py-1 rounded text-xs font-medium border ${
                    severityFilter.includes(sev)
                      ? statusBadgeClass("severity", sev) + " border-transparent"
                      : "bg-surface text-ink-subtle border-gray-300"
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
              className="text-xs text-ink-subtle hover:text-ink-muted underline pb-1"
            >
              Clear filters
            </button>
          )}
        </div>

        {/* Events List */}
        <div className="bg-surface rounded-lg border border-hairline">
          {loading ? (
            <ContentLoading />
          ) : loadError ? (
            <ErrorState message={loadError} onRetry={() => setReloadKey((k) => k + 1)} />
          ) : events.length === 0 ? (
            <div className="p-8 text-center text-ink-subtle" data-testid="activity-empty">
              No activity matches your filters.
            </div>
          ) : (
            <div className="divide-y divide-gray-100">
              {events.map((event) => (
                <div key={event.id} className="px-4 py-3 hover:bg-surface-muted" data-testid="activity-event">
                  <div className="flex items-start gap-3">
                    <div className="w-2 h-2 mt-2 rounded-full bg-bioaf-500 flex-shrink-0" />
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <p className="text-sm text-ink">{event.summary}</p>
                        {event.severity && (
                          <span
                            className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                              statusBadgeClass("severity", event.severity)
                            }`}
                          >
                            {event.severity}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-3 mt-1">
                        {event.user_email && (
                          <span className="text-xs text-ink-subtle">{event.user_email}</span>
                        )}
                        <span className="text-xs font-mono text-ink-subtle">{event.event_type}</span>
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
                            <span className="text-xs text-ink-subtle">
                              {event.entity_type} #{event.entity_id}
                            </span>
                          )
                        )}
                        <span className="text-xs text-ink-subtle">
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
    </>
  );
}
