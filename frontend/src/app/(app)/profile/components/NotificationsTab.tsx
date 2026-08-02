"use client";

import { useEffect, useState } from "react";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { api } from "@/lib/api";
import { getCurrentUser } from "@/lib/auth";

interface Preference {
  event_type: string;
  channel: string;
  enabled: boolean;
}

interface EventDef {
  type: string;
  label: string;
  description: string;
}

interface EventCategory {
  label: string;
  description: string;
  events: EventDef[];
}

const EVENT_CATEGORIES: EventCategory[] = [
  {
    label: "Pipelines & Analysis",
    description: "Pipeline runs, QC results, and analysis outputs",
    events: [
      { type: "pipeline.started", label: "Pipeline started", description: "A pipeline run began" },
      { type: "pipeline.completed", label: "Pipeline completed", description: "A pipeline run finished successfully" },
      { type: "pipeline.failed", label: "Pipeline failed", description: "A pipeline run failed" },
      { type: "pipeline.stage_error", label: "Pipeline stage error", description: "A pipeline stage encountered an error" },
      { type: "pipeline.oom", label: "Pipeline out of memory", description: "A pipeline run ran out of memory" },
      { type: "pipeline_run.reviewed", label: "Pipeline run reviewed", description: "A pipeline run was reviewed" },
      { type: "pipeline_run.review_reminder", label: "Review reminder", description: "A pipeline run is waiting for review" },
      { type: "qc.results_ready", label: "QC results ready", description: "Quality control results are available" },
      { type: "results.published", label: "Results published", description: "Analysis results were published" },
    ],
  },
  {
    label: "Experiments & Data",
    description: "Experiment progress, file uploads, and data ingestion",
    events: [
      { type: "experiment.status_changed", label: "Experiment status changed", description: "An experiment moved to a new stage" },
      { type: "data.uploaded", label: "File uploaded", description: "A file was uploaded to the platform" },
      { type: "ingest.files_cataloged", label: "Files cataloged", description: "Uploaded files were cataloged and linked" },
      { type: "ingest.batch_complete", label: "Ingestion complete", description: "A file ingestion batch finished" },
      { type: "ingest.failure", label: "Ingestion failed", description: "A file ingestion batch failed" },
      { type: "ingest.unclaimed_entity", label: "Unclaimed entity", description: "An ingested entity could not be matched to a project" },
      { type: "ingest.unmatched_file", label: "Unmatched file", description: "An uploaded file could not be matched to a sample" },
      { type: "ingest.duplicate_file", label: "Duplicate file detected", description: "A duplicate file was found during ingestion" },
      { type: "reference.deprecated", label: "Reference data deprecated", description: "A reference dataset was marked as deprecated" },
      { type: "sequencing_batch.detected", label: "Sequencing batch detected", description: "A new sequencing batch was found" },
      { type: "sequencing_batch.file_verified", label: "Sequencing file verified", description: "A file in a sequencing batch passed verification" },
      { type: "sequencing_batch.complete", label: "Sequencing batch complete", description: "All files in a sequencing batch arrived" },
      { type: "sequencing_batch.partial", label: "Sequencing batch incomplete", description: "A sequencing batch is missing files" },
    ],
  },
  {
    label: "Literature",
    description: "Paper searches, AI literature reviews, and library activity",
    events: [
      { type: "literature.paper_uploaded", label: "Paper added", description: "A paper was added to the Library" },
      { type: "literature.search_completed", label: "Search completed", description: "A literature search finished" },
      { type: "literature.search_failed", label: "Search failed", description: "A literature search could not complete" },
      { type: "literature.review_run_completed", label: "AI review completed", description: "An AI literature review run finished" },
      { type: "literature.review_run_failed", label: "AI review failed", description: "An AI literature review run failed" },
      { type: "literature.auto_review_recommendations", label: "New recommendations", description: "An automated review produced paper recommendations" },
      { type: "literature.comment_replied", label: "Comment reply", description: "Someone replied to your comment on a paper" },
      { type: "literature.paper_dismissed", label: "Paper dismissed", description: "A recommended paper was dismissed" },
    ],
  },
  {
    label: "Lab Knowledge",
    description: "Scientific Decision Records and glossary scans",
    events: [
      { type: "sdr_owner_assigned", label: "Decision record assigned", description: "You were made the owner of a Scientific Decision Record" },
      { type: "sdr_reassessment_flagged", label: "Decision record flagged", description: "A decision record was flagged for reassessment" },
      { type: "sdr_reassessment_warning", label: "Reassessment due soon", description: "A decision record is approaching its reassessment date" },
      { type: "lab_glossary_scan_complete", label: "Glossary scan complete", description: "A lab glossary scan finished" },
      { type: "lab_glossary_scan_failed", label: "Glossary scan failed", description: "A lab glossary scan could not complete" },
    ],
  },
  {
    label: "Infrastructure",
    description: "Platform components, compute resources, and backups",
    events: [
      { type: "component.health_degraded", label: "Component degraded", description: "A platform component is experiencing issues" },
      { type: "component.health_down", label: "Component down", description: "A platform component is unavailable" },
      { type: "compute.node_failure", label: "Compute node failure", description: "A compute node failed" },
      { type: "terraform.apply_failure", label: "Deployment failed", description: "An infrastructure deployment failed" },
      { type: "backup.failure", label: "Backup failed", description: "A scheduled backup failed" },
      { type: "session.idle", label: "Session idle", description: "Your notebook session has been idle" },
      { type: "work_node.launched", label: "Work node launched", description: "A work node started" },
      { type: "work_node.stopped", label: "Work node stopped", description: "A work node was shut down" },
      { type: "work_node.heartbeat_timeout", label: "Work node unresponsive", description: "A work node stopped reporting in" },
    ],
  },
  {
    label: "Budget & Storage",
    description: "Spending thresholds, storage limits, and quotas",
    events: [
      { type: "budget.threshold_50", label: "Budget at 50%", description: "Spending has reached 50% of the budget" },
      { type: "budget.threshold_80", label: "Budget at 80%", description: "Spending has reached 80% of the budget" },
      { type: "budget.threshold_100", label: "Budget exceeded", description: "Spending has exceeded the budget" },
      { type: "storage.threshold", label: "Storage limit approaching", description: "Storage usage is nearing its limit" },
      { type: "quota.warning", label: "Quota warning", description: "A resource quota is close to its limit" },
    ],
  },
  {
    label: "Automation",
    description: "Automated pipeline triggers and scheduling",
    events: [
      { type: "trigger.auto_run_submitted", label: "Pipeline auto-started", description: "A pipeline was automatically triggered" },
      { type: "trigger.run_queued_budget", label: "Pipeline queued (budget hold)", description: "A triggered pipeline was queued due to budget limits" },
      { type: "trigger.run_queued_exhausted", label: "Pipeline queued (budget exhausted)", description: "A triggered pipeline was queued because the budget is exhausted" },
      { type: "trigger.budget_mid_queue", label: "Budget changed while queued", description: "Budget status changed while pipelines were queued" },
      { type: "trigger.evaluation_failed", label: "Trigger evaluation failed", description: "A pipeline trigger rule could not be evaluated" },
      { type: "trigger.batch_window_closed", label: "Batch window closed", description: "A scheduling batch window has ended" },
      { type: "auto_run.launched", label: "Auto-run launched", description: "An automatic run started" },
      { type: "auto_run.cancelled", label: "Auto-run cancelled", description: "An automatic run was cancelled" },
      { type: "auto_run.budget_disabled", label: "Auto-run disabled (budget)", description: "Automatic runs were disabled because the budget was exhausted" },
    ],
  },
  {
    label: "Team & Platform",
    description: "Team activity and platform updates",
    events: [
      { type: "user.invitation_accepted", label: "Invitation accepted", description: "A user accepted their invitation" },
      { type: "platform.update_available", label: "Platform update available", description: "A new platform version is available" },
    ],
  },
];

/**
 * `defaultOn` is what the server does for an event with NO stored preference row, and it MUST match
 * the backend (notification_router.DEFAULT_ON_CHANNELS): in-app is the platform's default surface,
 * email is opt-in. Rendering every email toggle as "on" while the server treats absent-as-off (or
 * vice versa) makes the page lie about what will actually be delivered.
 */
/**
 * `defaultOn` is what the server does for an event with NO stored preference row, and it MUST match
 * the backend (notification_router.DEFAULT_ON_CHANNELS): in-app is the platform's default surface,
 * email is opt-in.
 *
 * `adminOnly` hides a channel from the toggles without removing it from the model: Slack routing is
 * org-level (a post goes to a shared channel, and Settings -> Slack picks the event types per
 * channel), so it is an admin concern rather than a personal one. A hidden channel's stored
 * preferences are still loaded and saved untouched.
 */
const CHANNELS: { key: string; label: string; defaultOn: boolean; adminOnly?: boolean }[] = [
  { key: "in_app", label: "In-App", defaultOn: true },
  { key: "email", label: "Email", defaultOn: false },
  { key: "slack", label: "Slack", defaultOn: true, adminOnly: true },
];

/** Fixed width for one channel column, shared by the header labels and the toggles below them. */
const CHANNEL_COL = "w-12";

const channelDefault = (channel: string): boolean =>
  CHANNELS.find((c) => c.key === channel)?.defaultOn ?? true;

export function NotificationsTab() {
  const isAdmin = getCurrentUser()?.role_name === "admin";
  // Hidden channels stay in `preferences` so a save never drops what it did not render.
  const visibleChannels = CHANNELS.filter((c) => !c.adminOnly || isAdmin);
  const [preferences, setPreferences] = useState<Preference[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    const load = async () => {
      try {
        const data = await api.get<Preference[]>("/api/notifications/preferences");
        setPreferences(data);
        setLoadFailed(false);
      } catch {
        // Never swallow this: with no preferences loaded every toggle falls back to its channel
        // default, and saving from that state would write those defaults over what the user
        // actually has stored. Say so, and refuse to save until a real load succeeds.
        setLoadFailed(true);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  const isEnabled = (eventType: string, channel: string): boolean => {
    const pref = preferences.find(
      (p) => p.event_type === eventType && p.channel === channel,
    );
    return pref ? pref.enabled : channelDefault(channel);
  };

  const toggle = (eventType: string, channel: string) => {
    const existing = preferences.find(
      (p) => p.event_type === eventType && p.channel === channel,
    );
    if (existing) {
      setPreferences(
        preferences.map((p) =>
          p.event_type === eventType && p.channel === channel
            ? { ...p, enabled: !p.enabled }
            : p,
        ),
      );
    } else {
      // No stored row yet, so this click flips the channel's default: turning a default-off
      // channel (email) ON must persist an explicit opt-in, not another "off".
      setPreferences([
        ...preferences,
        { event_type: eventType, channel, enabled: !channelDefault(channel) },
      ]);
    }
  };

  const toggleCategory = (category: EventCategory, channel: string) => {
    const allEnabled = category.events.every((e) => isEnabled(e.type, channel));
    const newEnabled = !allEnabled;

    setPreferences((prev) => {
      const updated = [...prev];
      for (const event of category.events) {
        const idx = updated.findIndex(
          (p) => p.event_type === event.type && p.channel === channel,
        );
        if (idx >= 0) {
          updated[idx] = { ...updated[idx], enabled: newEnabled };
        } else {
          updated.push({ event_type: event.type, channel, enabled: newEnabled });
        }
      }
      return updated;
    });
  };

  const isCategoryAllEnabled = (category: EventCategory, channel: string): boolean => {
    return category.events.every((e) => isEnabled(e.type, channel));
  };

  const isCategorySomeEnabled = (category: EventCategory, channel: string): boolean => {
    const enabled = category.events.filter((e) => isEnabled(e.type, channel));
    return enabled.length > 0 && enabled.length < category.events.length;
  };

  const handleSave = async () => {
    setSaving(true);
    setMessage("");
    try {
      await api.put("/api/notifications/preferences", { preferences });
      setMessage("Preferences saved");
    } catch {
      setMessage("Failed to save preferences");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">
            Notification Preferences
          </h2>
          <p className="text-sm text-gray-500 mt-1">
            Choose how you want to be notified about platform events. In-app is on by default and
            email is off until you turn it on.
            {isAdmin && " Slack posts to shared channels, and which events reach each channel is set in Settings, Slack."}
          </p>
        </div>
        <button
          onClick={handleSave}
          disabled={saving || loadFailed}
          className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700 disabled:opacity-50 text-sm font-medium"
        >
          {saving ? "Saving..." : "Save Preferences"}
        </button>
      </div>

      {loadFailed && (
        <div className="mb-4 p-3 rounded text-sm bg-red-50 border border-red-200 text-red-700">
          Could not load your notification preferences. Reload the page before changing anything:
          saving now would overwrite your saved settings with the defaults shown below.
        </div>
      )}

      {message && (
        <div className={`mb-4 p-3 rounded text-sm ${
          message.includes("Failed")
            ? "bg-red-50 border border-red-200 text-red-700"
            : "bg-green-50 border border-green-200 text-green-700"
        }`}>
          {message}
        </div>
      )}

      {loading ? (
        <ContentLoading />
      ) : (
        <div className="space-y-6">
          {EVENT_CATEGORIES.map((category) => (
            <div
              key={category.label}
              className="bg-white rounded-lg border border-gray-200 overflow-hidden"
            >
              <div className="px-4 py-3 bg-gray-50 border-b border-gray-200">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-sm font-semibold text-gray-900">
                      {category.label}
                    </h3>
                    <p className="text-xs text-gray-500 mt-0.5">
                      {category.description}
                    </p>
                  </div>
                  <div className="flex items-center gap-6">
                    {visibleChannels.map((ch) => (
                      <button
                        key={ch.key}
                        onClick={() => toggleCategory(category, ch.key)}
                        className={`${CHANNEL_COL} flex flex-col items-center gap-1`}
                        title={`Toggle all ${ch.label} for ${category.label}`}
                      >
                        <span className="text-xs text-gray-500">{ch.label}</span>
                        <span
                          className={`w-4 h-4 rounded border-2 flex items-center justify-center text-xs ${
                            isCategoryAllEnabled(category, ch.key)
                              ? "bg-bioaf-600 border-bioaf-600 text-white"
                              : isCategorySomeEnabled(category, ch.key)
                                ? "bg-white border-bioaf-400 text-bioaf-600"
                                : "bg-white border-gray-300"
                          }`}
                        >
                          {isCategoryAllEnabled(category, ch.key) && (
                            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                            </svg>
                          )}
                          {isCategorySomeEnabled(category, ch.key) && (
                            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14" />
                            </svg>
                          )}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              <div className="divide-y divide-gray-100">
                {category.events.map((event) => (
                  <div
                    key={event.type}
                    className="flex items-center justify-between px-4 py-2.5 hover:bg-gray-50"
                  >
                    <div className="min-w-0 flex-1 mr-4">
                      <p className="text-sm text-gray-900">{event.label}</p>
                      <p className="text-xs text-gray-400">{event.description}</p>
                    </div>
                    <div className="flex items-center gap-6">
                      {visibleChannels.map((ch) => (
                        <div key={ch.key} className={`${CHANNEL_COL} flex justify-center`}>
                          <button
                            onClick={() => toggle(event.type, ch.key)}
                            aria-label={`${ch.label} notifications for ${event.label}`}
                            aria-pressed={isEnabled(event.type, ch.key)}
                            className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                              isEnabled(event.type, ch.key)
                                ? "bg-bioaf-600"
                                : "bg-gray-300"
                            }`}
                          >
                            <span
                              className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${
                                isEnabled(event.type, ch.key)
                                  ? "translate-x-4"
                                  : "translate-x-0.5"
                              }`}
                            />
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
