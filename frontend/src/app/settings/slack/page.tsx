"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { api } from "@/lib/api";

interface SlackStatus {
  configured: boolean;
  connected: boolean;
  team_name: string | null;
  team_id: string | null;
  installed_by: string | null;
  installed_at: string | null;
  enabled: boolean;
}

interface SlackChannel {
  id: string;
  name: string;
  is_private: boolean;
}

interface ChannelMapping {
  id: number;
  channel_id: string;
  channel_name: string;
  event_types_json: string[];
  enabled: boolean;
}

interface SlackManifest {
  display_information: { name: string; description: string };
  features: { bot_user: { display_name: string; always_online: boolean } };
  oauth_config: { scopes: { bot: string[] }; redirect_urls: string[] };
  settings: { org_deploy_enabled: boolean; socket_mode_enabled: boolean; token_rotation_enabled: boolean };
}

interface TestWebhookResult {
  webhook: string;
  status: string;
  detail?: string;
}

interface TestResult {
  channel: string;
  status: string;
  detail?: string;
  webhooks?: TestWebhookResult[];
}

const EVENT_CATEGORIES: Record<string, { label: string; events: string[] }> = {
  pipelines: {
    label: "Pipelines & Analysis",
    events: [
      "pipeline.completed",
      "pipeline.failed",
      "pipeline.stage_error",
      "pipeline.run_reviewed",
      "pipeline.run_review_reminder",
      "qc.results_ready",
    ],
  },
  experiments: {
    label: "Experiments & Data",
    events: [
      "experiment.status_changed",
      "results.published",
      "data.uploaded",
      "reference.deprecated",
    ],
  },
  ingest: {
    label: "Ingest & Files",
    events: [
      "ingest.files_cataloged",
      "ingest.unclaimed_entity",
      "ingest.unmatched_file",
      "ingest.duplicate_file",
      "ingest.failure",
      "ingest.batch_complete",
    ],
  },
  infrastructure: {
    label: "Infrastructure",
    events: [
      "compute.node_failure",
      "component.health_degraded",
      "component.health_down",
      "backup.failure",
      "terraform.apply_failure",
      "work_node.heartbeat_timeout",
    ],
  },
  budget: {
    label: "Budget & Storage",
    events: [
      "budget.threshold_50",
      "budget.threshold_80",
      "budget.threshold_100",
      "quota.warning",
      "storage.threshold",
    ],
  },
  automation: {
    label: "Automation",
    events: [
      "trigger.auto_run_submitted",
      "trigger.run_queued_budget",
      "trigger.run_queued_exhausted",
      "trigger.evaluation_failed",
      "trigger.batch_window_closed",
    ],
  },
};

export default function SettingsSlackPage() {
  const [status, setStatus] = useState<SlackStatus | null>(null);
  const [channels, setChannels] = useState<SlackChannel[]>([]);
  const [mappings, setMappings] = useState<ChannelMapping[]>([]);
  const [manifest, setManifest] = useState<SlackManifest | null>(null);
  const [selectedChannel, setSelectedChannel] = useState("");
  const [selectedEvents, setSelectedEvents] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [copied, setCopied] = useState(false);
  const [savingCreds, setSavingCreds] = useState(false);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [signingSecret, setSigningSecret] = useState("");
  const [editingMappingId, setEditingMappingId] = useState<number | null>(null);
  const [editEvents, setEditEvents] = useState<string[]>([]);
  const [testResults, setTestResults] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const manifestRef = useRef<HTMLPreElement>(null);

  const loadStatus = useCallback(async () => {
    try {
      const data = await api.get<SlackStatus>("/api/notifications/slack/status");
      setStatus(data);
      return data;
    } catch {
      return null;
    }
  }, []);

  const loadChannels = useCallback(async () => {
    try {
      const data = await api.get<SlackChannel[]>("/api/notifications/slack/channels");
      setChannels(data);
    } catch {
      // Not connected or error
    }
  }, []);

  const loadMappings = useCallback(async () => {
    try {
      const data = await api.get<ChannelMapping[]>("/api/notifications/slack/channel-mappings");
      setMappings(data);
    } catch {
      // Not connected
    }
  }, []);

  useEffect(() => {
    const init = async () => {
      setLoading(true);
      const st = await loadStatus();
      if (st?.connected) {
        await Promise.all([loadChannels(), loadMappings()]);
      }
      setLoading(false);
    };
    init();
  }, [loadStatus, loadChannels, loadMappings]);

  // Handle OAuth callback redirect
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("connected") === "true") {
      setMessage("Slack connected successfully");
      window.history.replaceState({}, "", "/settings/slack");
      loadStatus().then((st) => {
        if (st?.connected) {
          loadChannels();
          loadMappings();
        }
      });
    }
  }, [loadStatus, loadChannels, loadMappings]);

  const handleGenerateManifest = async () => {
    setError("");
    try {
      const origin = encodeURIComponent(window.location.origin);
      const data = await api.get<SlackManifest>(`/api/notifications/slack/manifest?origin=${origin}`);
      setManifest(data);
    } catch {
      setError("Failed to generate manifest");
    }
  };

  const handleSaveCredentials = async () => {
    if (!clientId.trim() || !clientSecret.trim() || !signingSecret.trim()) {
      setError("All three fields are required");
      return;
    }
    setSavingCreds(true);
    setError("");
    try {
      await api.post("/api/notifications/slack/credentials", {
        client_id: clientId.trim(),
        client_secret: clientSecret.trim(),
        signing_secret: signingSecret.trim(),
      });
      setMessage("Slack credentials saved");
      setClientId("");
      setClientSecret("");
      setSigningSecret("");
      // Reload status so UI transitions to "configured" state
      await loadStatus();
    } catch {
      setError("Failed to save credentials");
    } finally {
      setSavingCreds(false);
    }
  };

  const handleCopyManifest = async () => {
    if (!manifest) return;
    const text = JSON.stringify(manifest, null, 2);

    // navigator.clipboard requires HTTPS; use textarea fallback on HTTP
    let success = false;
    try {
      await navigator.clipboard.writeText(text);
      success = true;
    } catch {
      // Fallback for HTTP contexts
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      success = document.execCommand("copy");
      document.body.removeChild(textarea);
    }

    if (success) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleStartOver = async () => {
    setError("");
    try {
      await api.delete("/api/notifications/slack/credentials");
      setStatus((prev) => prev ? { ...prev, configured: false, connected: false, team_name: null, team_id: null, installed_by: null, installed_at: null, enabled: false } : prev);
      setChannels([]);
      setMappings([]);
      setManifest(null);
      setMessage("Slack integration cleared. You can set it up again.");
    } catch {
      setError("Failed to clear Slack integration");
    }
  };

  const handleConnect = async () => {
    setConnecting(true);
    setError("");
    try {
      const data = await api.get<{ auth_url: string }>("/api/notifications/slack/auth-url");
      window.location.href = data.auth_url;
    } catch {
      setError("Failed to start Slack connection. Make sure you have set BIOAF_SLACK_CLIENT_ID and BIOAF_SLACK_CLIENT_SECRET from your Slack App credentials.");
      setConnecting(false);
    }
  };

  const handleDisconnect = async () => {
    setError("");
    try {
      await api.delete("/api/notifications/slack/disconnect");
      setStatus((prev) => prev ? { ...prev, connected: false, team_name: null, team_id: null, installed_by: null, installed_at: null, enabled: false } : prev);
      setChannels([]);
      setMappings([]);
      setMessage("Slack disconnected");
    } catch {
      setError("Failed to disconnect Slack");
    }
  };

  const handleAddMapping = async () => {
    if (!selectedChannel) return;
    setError("");
    const channel = channels.find((c) => c.id === selectedChannel);
    if (!channel) return;

    try {
      const mapping = await api.post<ChannelMapping>("/api/notifications/slack/channel-mappings", {
        channel_id: channel.id,
        channel_name: `#${channel.name}`,
        event_types: selectedEvents,
      });
      setMappings([...mappings, mapping]);
      setSelectedChannel("");
      setSelectedEvents([]);
      setMessage(`Added #${channel.name}`);
    } catch {
      setError("Failed to add channel mapping");
    }
  };

  const handleDeleteMapping = async (id: number) => {
    try {
      await api.delete(`/api/notifications/slack/channel-mappings/${id}`);
      setMappings(mappings.filter((m) => m.id !== id));
      setMessage("Channel mapping removed");
    } catch {
      setError("Failed to remove channel mapping");
    }
  };

  const handleToggleMapping = async (mapping: ChannelMapping) => {
    try {
      const updated = await api.put<ChannelMapping>(`/api/notifications/slack/channel-mappings/${mapping.id}`, {
        enabled: !mapping.enabled,
      });
      setMappings(mappings.map((m) => (m.id === mapping.id ? updated : m)));
    } catch {
      setError("Failed to update channel mapping");
    }
  };

  const handleTestSlack = async () => {
    if (mappings.length === 0) {
      setError("No channel mappings exist yet. Add a channel mapping first, then test.");
      return;
    }
    setTesting(true);
    setTestResults(null);
    setError("");
    setMessage("");
    try {
      const result = await api.post<TestResult>("/api/notifications/test", { channel: "slack" });
      setTestResults(result);
      if (result.status === "sent") {
        const allSent = result.webhooks?.every((w) => w.status === "sent");
        if (allSent) {
          setMessage("Test notification sent to all mapped channels.");
        }
      }
    } catch {
      setError("Failed to send test notification");
    } finally {
      setTesting(false);
    }
  };

  const handleStartEdit = (mapping: ChannelMapping) => {
    setEditingMappingId(mapping.id);
    setEditEvents([...mapping.event_types_json]);
  };

  const handleCancelEdit = () => {
    setEditingMappingId(null);
    setEditEvents([]);
  };

  const handleSaveEdit = async () => {
    if (editingMappingId === null) return;
    try {
      const updated = await api.put<ChannelMapping>(
        `/api/notifications/slack/channel-mappings/${editingMappingId}`,
        { event_types: editEvents }
      );
      setMappings(mappings.map((m) => (m.id === editingMappingId ? updated : m)));
      setEditingMappingId(null);
      setEditEvents([]);
      setMessage("Channel mapping updated");
    } catch {
      setError("Failed to update channel mapping");
    }
  };

  const toggleEditEvent = (event: string) => {
    setEditEvents((prev) =>
      prev.includes(event) ? prev.filter((e) => e !== event) : [...prev, event]
    );
  };

  const toggleEditCategory = (events: string[]) => {
    const allSelected = events.every((e) => editEvents.includes(e));
    if (allSelected) {
      setEditEvents((prev) => prev.filter((e) => !events.includes(e)));
    } else {
      setEditEvents((prev) => [...new Set([...prev, ...events])]);
    }
  };

  const toggleEvent = (event: string) => {
    setSelectedEvents((prev) =>
      prev.includes(event) ? prev.filter((e) => e !== event) : [...prev, event]
    );
  };

  const toggleCategory = (events: string[]) => {
    const allSelected = events.every((e) => selectedEvents.includes(e));
    if (allSelected) {
      setSelectedEvents((prev) => prev.filter((e) => !events.includes(e)));
    } else {
      setSelectedEvents((prev) => [...new Set([...prev, ...events])]);
    }
  };

  if (loading) {
    return (
      <div className="flex h-screen">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Header />
          <main className="flex-1 flex items-center justify-center">
            <p className="text-gray-500">Loading Slack settings...</p>
          </main>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="max-w-4xl">
            <div className="flex items-center justify-between mb-6">
              <div>
                <h1 className="text-2xl font-bold">Slack Integration</h1>
                <p className="text-sm text-gray-500 mt-1">
                  Connect your Slack workspace to receive bioAF notifications in channels you choose.
                </p>
              </div>
              {/* Test button is in the dedicated section below */}
            </div>

            {message && (
              <div className="mb-4 p-3 bg-green-50 border border-green-200 text-green-700 rounded text-sm">
                {message}
              </div>
            )}
            {error && (
              <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm">
                {error}
              </div>
            )}

            {/* Phase 1: Setup wizard (no Slack App configured yet) */}
            {!status?.configured && !status?.connected && (
              <div className="bg-white rounded-lg shadow p-6 mb-6">
                <h2 className="font-semibold mb-2">Set Up Slack App</h2>
                <p className="text-sm text-gray-600 mb-4">
                  Before connecting, you need to create a Slack App in your workspace.
                  Click the button below to generate the configuration, then follow the steps.
                </p>

                {!manifest ? (
                  <button
                    onClick={handleGenerateManifest}
                    className="px-4 py-2 bg-bioaf-600 text-white rounded hover:bg-bioaf-700 text-sm font-medium"
                  >
                    Generate Slack App Manifest
                  </button>
                ) : (
                  <div className="space-y-6">
                    {/* Step 1: Manifest */}
                    <div>
                      <h3 className="font-medium text-sm mb-2">Step 1: Copy the manifest</h3>
                      <div className="bg-gray-50 rounded-lg p-4">
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-sm font-medium text-gray-700">App Manifest (JSON)</span>
                          <button
                            onClick={handleCopyManifest}
                            className="text-xs px-3 py-1 bg-white border rounded hover:bg-gray-50 text-gray-700"
                          >
                            {copied ? "Copied" : "Copy to Clipboard"}
                          </button>
                        </div>
                        <pre
                          ref={manifestRef}
                          className="text-xs bg-white border rounded p-3 overflow-x-auto max-h-64 overflow-y-auto font-mono text-gray-800"
                        >
                          {JSON.stringify(manifest, null, 2)}
                        </pre>
                      </div>
                    </div>

                    {/* Step 2: Create the app in Slack */}
                    <div>
                      <h3 className="font-medium text-sm mb-2">Step 2: Create the app in Slack</h3>
                      <ol className="list-decimal list-inside space-y-2 text-sm text-gray-700">
                        <li>
                          Go to{" "}
                          <a
                            href="https://api.slack.com/apps"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-bioaf-600 hover:text-bioaf-700 underline"
                          >
                            api.slack.com/apps
                          </a>
                        </li>
                        <li>Click <span className="font-semibold">Create New App</span></li>
                        <li>Select <span className="font-semibold">From a manifest</span></li>
                        <li>Choose your workspace, then select <span className="font-semibold">JSON</span> as the format</li>
                        <li>Paste the manifest above and click <span className="font-semibold">Next</span></li>
                        <li>Review the summary and click <span className="font-semibold">Create</span></li>
                      </ol>
                    </div>

                    {/* Step 3: Paste credentials */}
                    <div>
                      <h3 className="font-medium text-sm mb-2">Step 3: Enter your app credentials</h3>
                      <p className="text-sm text-gray-500 mb-3">
                        After creating the app, you will land on its <span className="font-semibold">Basic Information</span> page.
                        Copy the three values below from the <span className="font-semibold">App Credentials</span> section.
                      </p>
                      <div className="space-y-3">
                        <div>
                          <label className="block text-xs font-medium text-gray-600 mb-1">Client ID</label>
                          <input
                            type="text"
                            value={clientId}
                            onChange={(e) => setClientId(e.target.value)}
                            placeholder="Paste your Client ID"
                            className="w-full px-3 py-2 border rounded text-sm font-mono"
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-medium text-gray-600 mb-1">Client Secret</label>
                          <input
                            type="password"
                            value={clientSecret}
                            onChange={(e) => setClientSecret(e.target.value)}
                            placeholder="Paste your Client Secret"
                            className="w-full px-3 py-2 border rounded text-sm font-mono"
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-medium text-gray-600 mb-1">Signing Secret</label>
                          <input
                            type="password"
                            value={signingSecret}
                            onChange={(e) => setSigningSecret(e.target.value)}
                            placeholder="Paste your Signing Secret"
                            className="w-full px-3 py-2 border rounded text-sm font-mono"
                          />
                        </div>
                        <button
                          onClick={handleSaveCredentials}
                          disabled={savingCreds || !clientId || !clientSecret || !signingSecret}
                          className="px-4 py-2 bg-bioaf-600 text-white rounded hover:bg-bioaf-700 text-sm font-medium disabled:opacity-50"
                        >
                          {savingCreds ? "Saving..." : "Save Credentials"}
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Phase 2: Configured but not connected */}
            {status?.configured && !status?.connected && (
              <div className="bg-white rounded-lg shadow p-6 mb-6">
                <h2 className="font-semibold mb-4">Connection</h2>
                <div className="text-center py-8">
                  <p className="text-gray-500 mb-4">
                    Connect bioAF to your Slack workspace to send notifications to channels.
                  </p>
                  <button
                    onClick={handleConnect}
                    disabled={connecting}
                    className="inline-flex items-center gap-2 px-5 py-2.5 bg-[#4A154B] text-white rounded-lg hover:bg-[#3a1039] disabled:opacity-50 font-medium"
                  >
                    <svg width="20" height="20" viewBox="0 0 54 54" xmlns="http://www.w3.org/2000/svg">
                      <path d="M19.712.133a5.381 5.381 0 0 0-5.376 5.387 5.381 5.381 0 0 0 5.376 5.386h5.376V5.52A5.381 5.381 0 0 0 19.712.133m0 14.365H5.376A5.381 5.381 0 0 0 0 19.884a5.381 5.381 0 0 0 5.376 5.387h14.336a5.381 5.381 0 0 0 5.376-5.387 5.381 5.381 0 0 0-5.376-5.386" fill="#36C5F0"/>
                      <path d="M53.76 19.884a5.381 5.381 0 0 0-5.376-5.386 5.381 5.381 0 0 0-5.376 5.386v5.387h5.376a5.381 5.381 0 0 0 5.376-5.387m-14.336 0V5.52A5.381 5.381 0 0 0 34.048.133a5.381 5.381 0 0 0-5.376 5.387v14.364a5.381 5.381 0 0 0 5.376 5.387 5.381 5.381 0 0 0 5.376-5.387" fill="#2EB67D"/>
                      <path d="M34.048 54a5.381 5.381 0 0 0 5.376-5.387 5.381 5.381 0 0 0-5.376-5.386h-5.376v5.386A5.381 5.381 0 0 0 34.048 54m0-14.365h14.336a5.381 5.381 0 0 0 5.376-5.386 5.381 5.381 0 0 0-5.376-5.387H34.048a5.381 5.381 0 0 0-5.376 5.387 5.381 5.381 0 0 0 5.376 5.386" fill="#ECB22E"/>
                      <path d="M0 34.249a5.381 5.381 0 0 0 5.376 5.386 5.381 5.381 0 0 0 5.376-5.386v-5.387H5.376A5.381 5.381 0 0 0 0 34.25m14.336-.001v14.364A5.381 5.381 0 0 0 19.712 54a5.381 5.381 0 0 0 5.376-5.387V34.249a5.381 5.381 0 0 0-5.376-5.387 5.381 5.381 0 0 0-5.376 5.387" fill="#E01E5A"/>
                    </svg>
                    {connecting ? "Connecting..." : "Add to Slack"}
                  </button>
                  <div className="mt-6">
                    <button
                      onClick={handleStartOver}
                      className="text-sm text-red-500 hover:text-red-700"
                    >
                      Start Over
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Phase 3: Connected */}
            {status?.connected && (
              <>
                {/* Connection status */}
                <div className="bg-white rounded-lg shadow p-6 mb-6">
                  <h2 className="font-semibold mb-4">Connection</h2>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className="w-3 h-3 rounded-full bg-green-500" />
                      <div>
                        <p className="font-medium">{status.team_name}</p>
                        <p className="text-xs text-gray-500">
                          Connected by {status.installed_by}
                          {status.installed_at && ` on ${new Date(status.installed_at).toLocaleDateString()}`}
                        </p>
                      </div>
                    </div>
                    <button
                      onClick={handleDisconnect}
                      className="text-sm text-red-500 hover:text-red-700"
                    >
                      Disconnect
                    </button>
                  </div>
                </div>

                {/* Channel mappings */}
                <div className="bg-white rounded-lg shadow p-6 mb-6">
                  <h2 className="font-semibold mb-2">Channel Mappings</h2>
                  <p className="text-sm text-gray-500 mb-4">
                    Choose which Slack channels receive bioAF notifications and what types of events each channel gets.
                  </p>

                  {/* Add new mapping */}
                  <div className="mb-6">
                    <div className="flex gap-3 mb-3">
                      <select
                        value={selectedChannel}
                        onChange={(e) => setSelectedChannel(e.target.value)}
                        className="flex-1 px-3 py-2 border rounded text-sm"
                      >
                        <option value="">Select a channel to add...</option>
                        {channels
                          .filter((ch) => !mappings.some((m) => m.channel_id === ch.id))
                          .map((ch) => (
                            <option key={ch.id} value={ch.id}>
                              {ch.is_private ? "🔒 " : "#"}{ch.name}
                            </option>
                          ))}
                      </select>
                    </div>

                    {selectedChannel && (
                      <div className="bg-gray-50 rounded p-4 mb-3">
                        <p className="text-sm font-medium mb-2">
                          Select which events to send to this channel
                        </p>
                        <p className="text-xs text-gray-500 mb-3">
                          Leave all unchecked to receive every event type.
                        </p>
                        <div className="space-y-4 mb-4">
                          {Object.entries(EVENT_CATEGORIES).map(([key, category]) => {
                            const allSelected = category.events.every((e) => selectedEvents.includes(e));
                            const someSelected = category.events.some((e) => selectedEvents.includes(e));
                            return (
                              <div key={key}>
                                <label className="flex items-center gap-2 text-sm font-medium mb-1 cursor-pointer">
                                  <input
                                    type="checkbox"
                                    checked={allSelected}
                                    ref={(el) => { if (el) el.indeterminate = someSelected && !allSelected; }}
                                    onChange={() => toggleCategory(category.events)}
                                    className="rounded"
                                  />
                                  {category.label}
                                </label>
                                <div className="ml-6 flex flex-wrap gap-x-4 gap-y-1">
                                  {category.events.map((event) => (
                                    <label key={event} className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer">
                                      <input
                                        type="checkbox"
                                        checked={selectedEvents.includes(event)}
                                        onChange={() => toggleEvent(event)}
                                        className="rounded"
                                      />
                                      {event.split(".").pop()?.replace(/_/g, " ")}
                                    </label>
                                  ))}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                        <button
                          onClick={handleAddMapping}
                          className="px-4 py-2 bg-bioaf-600 text-white rounded text-sm hover:bg-bioaf-700"
                        >
                          Save Channel Mapping
                        </button>
                      </div>
                    )}
                  </div>

                  {/* Existing mappings list */}
                  {mappings.length === 0 && !selectedChannel && (
                    <div className="text-center py-6 text-gray-400 text-sm border rounded border-dashed">
                      No channel mappings yet. Select a channel above to get started.
                    </div>
                  )}

                  {mappings.length > 0 && (
                    <div className="space-y-3">
                      {mappings.map((mapping) => (
                        <div key={mapping.id} className="border rounded">
                          <div className="flex items-center justify-between p-3">
                            <div className="flex items-center gap-3">
                              <button
                                onClick={() => handleToggleMapping(mapping)}
                                className={`w-8 h-5 rounded-full relative transition-colors ${
                                  mapping.enabled ? "bg-bioaf-600" : "bg-gray-300"
                                }`}
                              >
                                <span
                                  className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                                    mapping.enabled ? "left-3.5" : "left-0.5"
                                  }`}
                                />
                              </button>
                              <div>
                                <span className="font-medium text-sm">{mapping.channel_name}</span>
                                <span className="ml-2 text-xs text-gray-500">
                                  {mapping.event_types_json.length > 0
                                    ? `${mapping.event_types_json.length} event type${mapping.event_types_json.length !== 1 ? "s" : ""}`
                                    : "All events"}
                                </span>
                              </div>
                            </div>
                            <div className="flex items-center gap-3">
                              <button
                                onClick={() => editingMappingId === mapping.id ? handleCancelEdit() : handleStartEdit(mapping)}
                                className="text-xs text-bioaf-600 hover:text-bioaf-700"
                              >
                                {editingMappingId === mapping.id ? "Cancel" : "Edit"}
                              </button>
                              <button
                                onClick={() => handleDeleteMapping(mapping.id)}
                                className="text-xs text-red-500 hover:text-red-700"
                              >
                                Remove
                              </button>
                            </div>
                          </div>

                          {/* Expanded edit view */}
                          {editingMappingId === mapping.id && (
                            <div className="border-t bg-gray-50 p-4">
                              <p className="text-sm font-medium mb-2">Event types for {mapping.channel_name}</p>
                              <p className="text-xs text-gray-500 mb-3">
                                Leave all unchecked to receive every event type.
                              </p>
                              <div className="space-y-4 mb-4">
                                {Object.entries(EVENT_CATEGORIES).map(([key, category]) => {
                                  const allSelected = category.events.every((e) => editEvents.includes(e));
                                  const someSelected = category.events.some((e) => editEvents.includes(e));
                                  return (
                                    <div key={key}>
                                      <label className="flex items-center gap-2 text-sm font-medium mb-1 cursor-pointer">
                                        <input
                                          type="checkbox"
                                          checked={allSelected}
                                          ref={(el) => { if (el) el.indeterminate = someSelected && !allSelected; }}
                                          onChange={() => toggleEditCategory(category.events)}
                                          className="rounded"
                                        />
                                        {category.label}
                                      </label>
                                      <div className="ml-6 flex flex-wrap gap-x-4 gap-y-1">
                                        {category.events.map((event) => (
                                          <label key={event} className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer">
                                            <input
                                              type="checkbox"
                                              checked={editEvents.includes(event)}
                                              onChange={() => toggleEditEvent(event)}
                                              className="rounded"
                                            />
                                            {event.split(".").pop()?.replace(/_/g, " ")}
                                          </label>
                                        ))}
                                      </div>
                                    </div>
                                  );
                                })}
                              </div>
                              <button
                                onClick={handleSaveEdit}
                                className="px-4 py-2 bg-bioaf-600 text-white rounded text-sm hover:bg-bioaf-700"
                              >
                                Save Changes
                              </button>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Test channel mappings */}
                <div className="bg-white rounded-lg shadow p-6 mb-6">
                  <h2 className="font-semibold mb-2">Test Channel Mappings</h2>
                  <p className="text-sm text-gray-500 mb-4">
                    Send a test message to all mapped channels to verify everything is working.
                  </p>
                  <button
                    onClick={handleTestSlack}
                    disabled={testing}
                    className="px-4 py-2 bg-bioaf-600 text-white rounded text-sm hover:bg-bioaf-700 disabled:opacity-50"
                  >
                    {testing ? "Sending..." : "Test Channel Mappings"}
                  </button>

                  {testResults && (
                    <div className="mt-4 space-y-2">
                      {testResults.detail && (
                        <div className="p-3 bg-yellow-50 border border-yellow-200 text-yellow-800 rounded text-sm">
                          {testResults.detail}
                        </div>
                      )}
                      {testResults.webhooks?.map((result, i) => (
                        <div
                          key={i}
                          className={`p-3 rounded text-sm ${
                            result.status === "sent"
                              ? "bg-green-50 border border-green-200 text-green-700"
                              : "bg-red-50 border border-red-200 text-red-700"
                          }`}
                        >
                          <span className="font-medium">{result.webhook}</span>
                          {result.status === "sent" ? (
                            <span className="ml-2">-- Sent successfully</span>
                          ) : (
                            <span className="ml-2">-- {result.detail || "Failed to deliver"}</span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
