"use client";

import { useState } from "react";

import type { WidgetDefinition } from "@/components/dashboard/registry";

interface DashboardWidgetPickerProps {
  /** Widgets the user is permitted to use (already permission-filtered). */
  available: WidgetDefinition[];
  /** Currently-enabled widget keys. */
  enabledKeys: string[];
  onClose: () => void;
  onSave: (keys: string[]) => void;
}

export function DashboardWidgetPicker({
  available,
  enabledKeys,
  onClose,
  onSave,
}: DashboardWidgetPickerProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set(enabledKeys));

  const toggle = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const handleSave = () => {
    // Persist in catalog order (v1 has no custom ordering).
    onSave(available.filter((w) => selected.has(w.key)).map((w) => w.key));
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
      data-testid="widget-picker"
    >
      <div
        className="relative bg-white rounded-lg shadow-xl w-full max-w-lg mx-4 max-h-[80vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-white border-b px-5 py-4 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Customize dashboard</h2>
            <p className="text-xs text-gray-500">
              Choose the widgets you want. You only see widgets you have access to.
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-gray-400 hover:text-gray-600 text-2xl leading-none"
          >
            &times;
          </button>
        </div>

        <div className="p-5 space-y-1">
          {available.length === 0 && (
            <p className="text-sm text-gray-500" data-testid="picker-empty">
              No widgets are available for your role.
            </p>
          )}
          {available.map((w) => (
            <label
              key={w.key}
              data-testid={`picker-item-${w.key}`}
              className="flex items-start gap-3 p-2 rounded hover:bg-gray-50 cursor-pointer"
            >
              <input
                type="checkbox"
                checked={selected.has(w.key)}
                onChange={() => toggle(w.key)}
                data-testid={`picker-toggle-${w.key}`}
                className="mt-1 h-4 w-4 accent-bioaf-600"
              />
              <span>
                <span className="block text-sm font-medium text-gray-800">{w.title}</span>
                <span className="block text-xs text-gray-500">{w.description}</span>
              </span>
            </label>
          ))}
        </div>

        <div className="sticky bottom-0 bg-white border-t px-5 py-3 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            data-testid="picker-save"
            className="px-4 py-2 bg-bioaf-600 text-white rounded-lg hover:bg-bioaf-700 text-sm font-medium"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
