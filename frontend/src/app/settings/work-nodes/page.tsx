"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/layout/Sidebar";
import { Header } from "@/components/layout/Header";
import { isAuthenticated } from "@/lib/auth";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";

interface WorkNodeConfig {
  max_nodes_per_user: number;
  idle_timeout_hours: number;
}

export default function WorkNodeSettingsPage() {
  const router = useRouter();
  const { canAccess, loading: permLoading } = usePermissions();
  const [config, setConfig] = useState<WorkNodeConfig>({
    max_nodes_per_user: 2,
    idle_timeout_hours: 24,
  });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!isAuthenticated()) { router.push("/login"); return; }
    if (permLoading) return;
    if (!canAccess("work_nodes", "configure")) { router.push("/dashboard"); return; }
    loadConfig();
  }, [router, permLoading, canAccess]);

  async function loadConfig() {
    try {
      const data = await api.get<WorkNodeConfig>("/api/v1/settings/work-nodes");
      setConfig(data);
    } catch {}
  }

  async function handleSave() {
    setSaving(true);
    setMessage("");
    try {
      await api.put("/api/v1/settings/work-nodes", config);
      setMessage("Settings saved");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <h1 className="text-2xl font-bold mb-6">Work Node Settings</h1>

          {message && (
            <div className={`mb-4 p-3 rounded text-sm ${message === "Settings saved" ? "bg-green-50 border border-green-200 text-green-700" : "bg-red-50 border border-red-200 text-red-700"}`}>
              {message}
            </div>
          )}

          <div className="bg-white rounded-lg shadow p-6 max-w-2xl space-y-6">
            <div>
              <label htmlFor="max-nodes" className="block text-sm font-medium text-gray-700 mb-1">
                Max Work Nodes Per User
              </label>
              <input
                id="max-nodes"
                type="number"
                min={1}
                max={50}
                value={config.max_nodes_per_user}
                onChange={(e) => setConfig({ ...config, max_nodes_per_user: Number(e.target.value) })}
                className="border rounded px-3 py-2 text-sm w-32"
              />
              <p className="text-xs text-gray-500 mt-1">
                Maximum concurrent SSH sessions a single user can run (1-50)
              </p>
            </div>

            <div>
              <label htmlFor="idle-timeout" className="block text-sm font-medium text-gray-700 mb-1">
                Idle Timeout (hours)
              </label>
              <input
                id="idle-timeout"
                type="number"
                min={1}
                max={720}
                value={config.idle_timeout_hours}
                onChange={(e) => setConfig({ ...config, idle_timeout_hours: Number(e.target.value) })}
                className="border rounded px-3 py-2 text-sm w-32"
              />
              <p className="text-xs text-gray-500 mt-1">
                Auto-stop nodes with no heartbeat after this many hours (1-720)
              </p>
            </div>

            <button
              onClick={handleSave}
              disabled={saving}
              className="bg-bioaf-600 text-white px-6 py-2 rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50"
            >
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </main>
      </div>
    </div>
  );
}
