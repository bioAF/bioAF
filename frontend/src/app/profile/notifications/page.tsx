"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { isAuthenticated } from "@/lib/auth";
import { ContentLoading } from "@/components/shared/ContentLoading";
import { api } from "@/lib/api";

interface Preference {
  event_type: string;
  channel: string;
  enabled: boolean;
}

const EVENT_TYPES = [
  "pipeline.completed",
  "pipeline.failed",
  "pipeline.stage_error",
  "qc.results_ready",
  "experiment.status_changed",
  "budget.threshold_50",
  "budget.threshold_80",
  "budget.threshold_100",
  "compute.node_failure",
  "component.health_degraded",
  "component.health_down",
  "backup.failure",
  "quota.warning",
  "session.idle",
  "results.published",
  "data.uploaded",
  "platform.update_available",
  "storage.threshold",
  "user.invitation_accepted",
  "terraform.apply_failure",
];

const CHANNELS = ["in_app", "email", "slack"];

export default function NotificationPreferencesPage() {
  const router = useRouter();
  const [preferences, setPreferences] = useState<Preference[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!isAuthenticated()) {
      router.push("/login");
      return;
    }
    const load = async () => {
      try {
        const data = await api.get<Preference[]>("/api/notifications/preferences");
        setPreferences(data);
      } catch {
        // ignore
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [router]);

  const isEnabled = (eventType: string, channel: string): boolean => {
    const pref = preferences.find(
      (p) => p.event_type === eventType && p.channel === channel
    );
    return pref ? pref.enabled : true;
  };

  const toggle = (eventType: string, channel: string) => {
    const existing = preferences.find(
      (p) => p.event_type === eventType && p.channel === channel
    );
    if (existing) {
      setPreferences(
        preferences.map((p) =>
          p.event_type === eventType && p.channel === channel
            ? { ...p, enabled: !p.enabled }
            : p
        )
      );
    } else {
      setPreferences([
        ...preferences,
        { event_type: eventType, channel, enabled: false },
      ]);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setMessage("");
    try {
      await api.put("/api/notifications/preferences", { preferences });
      setMessage("Preferences saved successfully");
    } catch {
      setMessage("Failed to save preferences");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold text-gray-900 mb-6">
            Notification Preferences
          </h1>

          {message && (
            <div className="mb-4 p-3 rounded bg-green-50 text-green-700 text-sm">
              {message}
            </div>
          )}

          {loading ? (
            <ContentLoading />
          ) : (
            <>
              <div className="bg-white rounded-lg border border-gray-200 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b bg-gray-50">
                      <th className="text-left px-4 py-3 font-medium text-gray-700">
                        Event Type
                      </th>
                      {CHANNELS.map((ch) => (
                        <th key={ch} className="text-center px-4 py-3 font-medium text-gray-700">
                          {ch.replace("_", " ")}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {EVENT_TYPES.map((et) => (
                      <tr key={et} className="border-b hover:bg-gray-50">
                        <td className="px-4 py-2.5 text-gray-900 font-mono text-xs">
                          {et}
                        </td>
                        {CHANNELS.map((ch) => (
                          <td key={ch} className="text-center px-4 py-2.5">
                            <button
                              onClick={() => toggle(et, ch)}
                              className={`w-10 h-5 rounded-full relative transition-colors ${
                                isEnabled(et, ch)
                                  ? "bg-bioaf-600"
                                  : "bg-gray-300"
                              }`}
                            >
                              <span
                                className={`absolute top-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
                                  isEnabled(et, ch)
                                    ? "translate-x-5"
                                    : "translate-x-0.5"
                                }`}
                              />
                            </button>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="mt-4">
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="bg-bioaf-600 text-white px-4 py-2 rounded hover:bg-bioaf-700 disabled:opacity-50"
                >
                  {saving ? "Saving..." : "Save Preferences"}
                </button>
              </div>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
