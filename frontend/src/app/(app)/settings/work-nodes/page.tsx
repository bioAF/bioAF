"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { usePermissions } from "@/hooks/usePermissions";
import { api } from "@/lib/api";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import { useToast } from "@/components/shared/Toast";

interface WorkNodeConfig {
  max_nodes_per_user: number;
  idle_timeout_hours: number;
  boot_disk_gb: number;
  boot_disk_type: string;
}

const DISK_TYPES = ["pd-ssd", "pd-balanced", "pd-standard"] as const;

interface NotebookConfig {
  idle_timeout_hours: number;
  idle_warning_minutes: number;
  max_sessions_per_user: number;
}

function SaveBanner({ message }: { message: string }) {
  if (!message) return null;
  const ok = message === "Settings saved";
  return (
    <div
      className={`p-3 rounded text-sm ${
        ok
          ? "bg-green-50 border border-green-200 text-green-700"
          : "bg-red-50 border border-red-200 text-red-700"
      }`}
    >
      {message}
    </div>
  );
}

export default function WorkbenchSettingsPage() {
  const toast = useToast();
  const router = useRouter();
  const { canAccess, loading: permLoading } = usePermissions();

  const [workNodes, setWorkNodes] = useState<WorkNodeConfig>({
    max_nodes_per_user: 2,
    idle_timeout_hours: 24,
    boot_disk_gb: 100,
    boot_disk_type: "pd-ssd",
  });
  const [savingWorkNodes, setSavingWorkNodes] = useState(false);
  const [workNodeMessage, setWorkNodeMessage] = useState("");

  // idle_warning_minutes is loaded and preserved on save, but intentionally not
  // shown here; warning-before-shutdown does not belong in these admin limits.
  const [notebooks, setNotebooks] = useState<NotebookConfig>({
    idle_timeout_hours: 4,
    idle_warning_minutes: 15,
    max_sessions_per_user: 2,
  });
  const [savingNotebooks, setSavingNotebooks] = useState(false);
  const [notebookMessage, setNotebookMessage] = useState("");

  useEffect(() => {
    if (permLoading) return;
    if (!canAccess("work_nodes", "configure")) {
      router.push("/dashboard");
      return;
    }
    loadConfigs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router, permLoading, canAccess]);

  async function loadConfigs() {
    try {
      const data = await api.get<WorkNodeConfig>("/api/v1/settings/work-nodes");
      setWorkNodes(data);
    } catch (e) {
      logError("loading the work node settings", e);
      toast.error(loadFailureMessage("Work node settings"));
    }
    try {
      const data = await api.get<NotebookConfig>("/api/v1/settings/notebooks");
      setNotebooks((prev) => ({ ...prev, ...data }));
    } catch (e) {
      logError("loading the notebook settings", e);
      toast.error(loadFailureMessage("Notebook settings"));
    }
  }

  async function saveWorkNodes() {
    setSavingWorkNodes(true);
    setWorkNodeMessage("");
    try {
      await api.put("/api/v1/settings/work-nodes", workNodes);
      setWorkNodeMessage("Settings saved");
    } catch (err) {
      setWorkNodeMessage(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setSavingWorkNodes(false);
    }
  }

  async function saveNotebooks() {
    setSavingNotebooks(true);
    setNotebookMessage("");
    try {
      await api.put("/api/v1/settings/notebooks", {
        idle_timeout_hours: notebooks.idle_timeout_hours,
        idle_warning_minutes: notebooks.idle_warning_minutes,
        max_sessions_per_user: notebooks.max_sessions_per_user,
      });
      setNotebookMessage("Settings saved");
    } catch (err) {
      setNotebookMessage(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setSavingNotebooks(false);
    }
  }

  return (
    <main className="flex-1 overflow-y-auto p-6">
      <h1 className="text-2xl font-bold mb-6">Workbench Settings</h1>

      <div className="max-w-2xl space-y-8">
        {/* Work Nodes */}
        <section className="bg-white rounded-lg shadow p-6 space-y-6">
          <div>
            <h2 className="text-lg font-semibold">Work Nodes</h2>
            <p className="text-sm text-gray-500">Limits for GCE work-node sessions.</p>
          </div>

          <SaveBanner message={workNodeMessage} />

          <div>
            <label htmlFor="wn-max-nodes" className="block text-sm font-medium text-gray-700 mb-1">
              Max Work Nodes Per User
            </label>
            <input
              id="wn-max-nodes"
              type="number"
              min={1}
              max={50}
              value={workNodes.max_nodes_per_user}
              onChange={(e) => setWorkNodes({ ...workNodes, max_nodes_per_user: Number(e.target.value) })}
              className="border rounded px-3 py-2 text-sm w-32"
            />
            <p className="text-xs text-gray-500 mt-1">
              Maximum concurrent SSH sessions a single user can run (1-50)
            </p>
          </div>

          <div>
            <label htmlFor="wn-idle" className="block text-sm font-medium text-gray-700 mb-1">
              Idle Timeout (hours)
            </label>
            <input
              id="wn-idle"
              type="number"
              min={1}
              max={720}
              value={workNodes.idle_timeout_hours}
              onChange={(e) => setWorkNodes({ ...workNodes, idle_timeout_hours: Number(e.target.value) })}
              className="border rounded px-3 py-2 text-sm w-32"
            />
            <p className="text-xs text-gray-500 mt-1">
              Auto-stop nodes with no heartbeat after this many hours (1-720)
            </p>
          </div>

          <div>
            <label htmlFor="wn-disk-gb" className="block text-sm font-medium text-gray-700 mb-1">
              Boot Disk Size (GB)
            </label>
            <input
              id="wn-disk-gb"
              type="number"
              min={20}
              max={1000}
              value={workNodes.boot_disk_gb}
              onChange={(e) => setWorkNodes({ ...workNodes, boot_disk_gb: Number(e.target.value) })}
              className="border rounded px-3 py-2 text-sm w-32"
            />
            <p className="text-xs text-gray-500 mt-1">
              Per-node boot disk size (20-1000 GB). pd-ssd and pd-balanced both consume the
              regional SSD quota; lower this or use pd-standard if launches fail with
              SSD_TOTAL_GB quota errors.
            </p>
          </div>

          <div>
            <label htmlFor="wn-disk-type" className="block text-sm font-medium text-gray-700 mb-1">
              Boot Disk Type
            </label>
            <select
              id="wn-disk-type"
              value={workNodes.boot_disk_type}
              onChange={(e) => setWorkNodes({ ...workNodes, boot_disk_type: e.target.value })}
              className="border rounded px-3 py-2 text-sm w-48"
            >
              {DISK_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <p className="text-xs text-gray-500 mt-1">
              pd-standard uses the separate (larger) HDD quota but is slower than SSD.
            </p>
          </div>

          <button
            onClick={saveWorkNodes}
            disabled={savingWorkNodes}
            className="bg-bioaf-600 text-white px-6 py-2 rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50"
          >
            {savingWorkNodes ? "Saving..." : "Save"}
          </button>
        </section>

        {/* Notebooks */}
        <section className="bg-white rounded-lg shadow p-6 space-y-6">
          <div>
            <h2 className="text-lg font-semibold">Notebooks</h2>
            <p className="text-sm text-gray-500">Limits for JupyterHub and RStudio sessions.</p>
          </div>

          <SaveBanner message={notebookMessage} />

          <div>
            <label htmlFor="nb-idle" className="block text-sm font-medium text-gray-700 mb-1">
              Idle Timeout (hours)
            </label>
            <input
              id="nb-idle"
              type="number"
              min={1}
              max={12}
              value={notebooks.idle_timeout_hours}
              onChange={(e) => setNotebooks({ ...notebooks, idle_timeout_hours: Number(e.target.value) })}
              className="border rounded px-3 py-2 text-sm w-32"
            />
            <p className="text-xs text-gray-500 mt-1">
              Sessions idle longer than this will be auto-terminated (1-12 hours)
            </p>
          </div>

          <div>
            <label htmlFor="nb-max-sessions" className="block text-sm font-medium text-gray-700 mb-1">
              Max Sessions Per User
            </label>
            <input
              id="nb-max-sessions"
              type="number"
              min={1}
              max={5}
              value={notebooks.max_sessions_per_user}
              onChange={(e) => setNotebooks({ ...notebooks, max_sessions_per_user: Number(e.target.value) })}
              className="border rounded px-3 py-2 text-sm w-32"
            />
          </div>

          <button
            onClick={saveNotebooks}
            disabled={savingNotebooks}
            className="bg-bioaf-600 text-white px-6 py-2 rounded-md text-sm hover:bg-bioaf-700 disabled:opacity-50"
          >
            {savingNotebooks ? "Saving..." : "Save"}
          </button>
        </section>
      </div>
    </main>
  );
}
