"use client";

import { useState } from "react";

import type { WidgetDefinition } from "@/components/dashboard/registry";
import { Modal } from "@/components/shared/Modal";
import { Button } from "@/components/ui/Button";

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
    <Modal
      open
      title="Customize dashboard"
      onClose={onClose}
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSave} data-testid="picker-save">
            Save
          </Button>
        </>
      }
    >
      <div data-testid="widget-picker">
        <p className="text-xs text-gray-500 mb-3">
          Choose the widgets you want. You only see widgets you have access to.
        </p>

        <div className="space-y-1">
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
      </div>
    </Modal>
  );
}
