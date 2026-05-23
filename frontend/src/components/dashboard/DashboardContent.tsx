"use client";

import { useState } from "react";

import { usePermissions } from "@/hooks/usePermissions";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { useDashboardLayout } from "@/components/dashboard/useDashboardLayout";
import { DashboardWidgetPicker } from "@/components/dashboard/DashboardWidgetPicker";
import {
  accessibleWidgets,
  canUseWidget,
  getWidget,
} from "@/components/dashboard/registry";

function GearIcon() {
  return (
    <svg
      className="w-5 h-5"
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
      />
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
      />
    </svg>
  );
}

export function DashboardContent() {
  const { canAccess, roleName, loading: permsLoading } = usePermissions();
  const { keys, loading: layoutLoading, save } = useDashboardLayout(roleName, !permsLoading);
  const [pickerOpen, setPickerOpen] = useState(false);

  const loading = permsLoading || layoutLoading || keys === null;

  // Render only enabled widgets that exist in the catalog AND the user can access.
  const visibleKeys = (keys ?? []).filter((key) => {
    const def = getWidget(key);
    return def !== undefined && canUseWidget(def, canAccess);
  });

  return (
    <div className="flex flex-col flex-1 min-h-0" data-testid="dashboard-content">
      <div className="flex items-center justify-between mb-4 shrink-0">
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <button
          data-testid="dashboard-gear"
          onClick={() => setPickerOpen(true)}
          aria-label="Customize dashboard"
          className="p-2 rounded-lg text-gray-500 hover:text-gray-800 hover:bg-gray-100"
        >
          <GearIcon />
        </button>
      </div>

      {loading ? (
        <div className="py-12" data-testid="dashboard-loading">
          <LoadingSpinner />
        </div>
      ) : visibleKeys.length === 0 ? (
        <div
          className="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-8 text-center text-sm text-gray-500"
          data-testid="dashboard-empty"
        >
          Your dashboard has no widgets.{" "}
          <button
            onClick={() => setPickerOpen(true)}
            className="text-bioaf-600 font-medium hover:underline"
          >
            Add widgets
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {visibleKeys.map((key) => {
            const Widget = getWidget(key)!.component;
            return <Widget key={key} />;
          })}
        </div>
      )}

      {pickerOpen && (
        <DashboardWidgetPicker
          available={accessibleWidgets(canAccess)}
          enabledKeys={visibleKeys}
          onClose={() => setPickerOpen(false)}
          onSave={(selected) => {
            save(selected);
            setPickerOpen(false);
          }}
        />
      )}
    </div>
  );
}
