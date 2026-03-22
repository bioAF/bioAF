"use client";

import { useState } from "react";
import Link from "next/link";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { api, ApiError } from "@/lib/api";
import type { ComponentState } from "@/lib/types";

interface ComponentCardProps {
  component: ComponentState;
  onAction: () => void;
  comingSoon?: boolean;
  comingSoonMessage?: string;
}

export function ComponentCard({ component, onAction, comingSoon, comingSoonMessage }: ComponentCardProps) {
  const [showConfirm, setShowConfirm] = useState(false);
  const [action, setAction] = useState<"enable" | "disable">("enable");
  const [error, setError] = useState("");

  const handleAction = async () => {
    setShowConfirm(false);
    setError("");
    try {
      const endpoint = action === "enable"
        ? `/api/components/${component.key}/enable`
        : `/api/components/${component.key}/disable`;
      await api.post(endpoint);
      onAction();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Action failed");
    }
  };

  if (comingSoon) {
    return (
      <div className="bg-white rounded-lg shadow p-6 border border-gray-200 opacity-60">
        <div className="flex items-start justify-between mb-3">
          <h3 className="font-semibold text-gray-400">{component.name}</h3>
          <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full font-medium">
            Coming Soon
          </span>
        </div>
        <p className="text-sm text-gray-400 mb-3">{component.description}</p>
        {comingSoonMessage && (
          <p className="text-xs text-gray-400 italic">{comingSoonMessage}</p>
        )}
      </div>
    );
  }

  return (
    <>
      <div className="bg-white rounded-lg shadow p-6 border border-gray-200 hover:shadow-md transition-shadow">
        <div className="flex items-start justify-between mb-3">
          <h3 className="font-semibold">{component.name}</h3>
          <StatusBadge status={component.status} />
        </div>
        <p className="text-sm text-gray-600 mb-3">{component.description}</p>

        {component.dependencies.length > 0 && (
          <p className="text-xs text-gray-400 mb-3">
            Requires: {component.dependencies.join(", ")}
          </p>
        )}

        <div className="flex items-center justify-end mt-4">
          <div className="flex gap-2">
            <Link
              href={`/components/${component.key}`}
              className="px-3 py-1 text-sm border border-gray-300 rounded hover:bg-gray-50"
            >
              Configure
            </Link>
            {component.enabled ? (
              <button
                onClick={() => { setAction("disable"); setShowConfirm(true); }}
                className="px-3 py-1 text-sm bg-red-50 text-red-700 rounded hover:bg-red-100"
              >
                Disable
              </button>
            ) : (
              <button
                onClick={() => { setAction("enable"); setShowConfirm(true); }}
                className="px-3 py-1 text-sm bg-bioaf-600 text-white rounded hover:bg-bioaf-700"
              >
                Enable
              </button>
            )}
          </div>
        </div>

        {error && <p className="text-xs text-red-600 mt-2">{error}</p>}
      </div>

      <ConfirmDialog
        open={showConfirm}
        title={`${action === "enable" ? "Enable" : "Disable"} ${component.name}?`}
        message={
          action === "enable"
            ? `This will provision ${component.name} infrastructure.`
            : `This will destroy ${component.name} infrastructure. This action cannot be undone.`
        }
        confirmLabel={action === "enable" ? "Enable" : "Disable"}
        variant={action === "disable" ? "danger" : "default"}
        onConfirm={handleAction}
        onCancel={() => setShowConfirm(false)}
      />
    </>
  );
}
