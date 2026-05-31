"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

export interface PickerComponent {
  key: string;
  name: string;
  description: string;
  category: string;
  dependencies: string[];
  cost_estimate: string;
  status: "available" | "coming_soon";
}

interface ComponentPickerProps {
  components: PickerComponent[];
  defaultSelected: string[];
  onChange: (selected: string[]) => void;
}

const CATEGORY_LABELS: Record<string, string> = {
  compute: "Compute",
  pipeline_orchestration: "Pipeline Orchestration",
  analysis: "Analysis",
  visualization: "Visualization",
  search: "Search",
};

const CATEGORY_ORDER = [
  "pipeline_orchestration",
  "analysis",
  "visualization",
  "search",
  "compute",
];

/** Components a user can toggle directly. Pool components are usually
 * auto-handled as dependencies. */
function isUserFacing(component: PickerComponent): boolean {
  return component.status === "available" && !component.key.endsWith("_pool");
}

export function ComponentPicker({
  components,
  defaultSelected,
  onChange,
}: ComponentPickerProps) {
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(defaultSelected)
  );

  const byKey = useMemo(() => {
    const map = new Map<string, PickerComponent>();
    for (const c of components) map.set(c.key, c);
    return map;
  }, [components]);

  /** Resolve transitively all deps of the given keys, restricted to deps that
   * actually exist in the visible component list. The K8s components depend on
   * `kubernetes_cluster`, which is always-on plumbing the wizard owns; it must
   * not leak into the user's selection or the batch endpoint will reject it.
   */
  const resolveDeps = useCallback(
    (keys: string[]): Set<string> => {
      const out = new Set<string>();
      const walk = (k: string) => {
        const c = byKey.get(k);
        if (!c) return;
        for (const dep of c.dependencies) {
          if (!byKey.has(dep)) continue;
          if (!out.has(dep)) {
            out.add(dep);
            walk(dep);
          }
        }
      };
      for (const k of keys) walk(k);
      return out;
    },
    [byKey]
  );

  useEffect(() => {
    onChange(Array.from(selected));
  }, [selected, onChange]);

  const toggle = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (prev.has(key)) {
        next.delete(key);
        // Drop deps that no remaining selection needs.
        const stillNeeded = resolveDeps(Array.from(next));
        for (const c of components) {
          if (
            c.dependencies.length === 0 &&
            !next.has(c.key)
          ) {
            continue;
          }
          if (next.has(c.key) && !isUserFacing(c) && !stillNeeded.has(c.key)) {
            // user-selected the dep directly; leave it
          }
          if (!isUserFacing(c) && next.has(c.key) && !stillNeeded.has(c.key)) {
            next.delete(c.key);
          }
        }
      } else {
        next.add(key);
        for (const dep of resolveDeps([key])) {
          next.add(dep);
        }
      }
      return next;
    });
  };

  const visibleCategories = CATEGORY_ORDER.filter((cat) =>
    components.some((c) => c.category === cat)
  );

  return (
    <div className="space-y-6">
      {visibleCategories.map((category) => (
        <div key={category}>
          <h3 className="text-sm font-semibold text-gray-700 mb-2">
            {CATEGORY_LABELS[category] ?? category}
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {components
              .filter((c) => c.category === category)
              .map((c) =>
                c.status === "coming_soon" ? (
                  <div
                    key={c.key}
                    data-testid="component-card"
                    className="bg-white rounded-lg shadow p-4 border border-gray-200 opacity-60"
                  >
                    <div className="flex items-start justify-between mb-2">
                      <span className="font-medium text-sm text-gray-400">
                        {c.name}
                      </span>
                      <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full font-medium">
                        Coming Soon
                      </span>
                    </div>
                    <p className="text-xs text-gray-400">{c.description}</p>
                  </div>
                ) : (
                  <label
                    key={c.key}
                    data-testid="component-card"
                    className="flex items-start gap-3 bg-white rounded-lg shadow p-4 border border-gray-200 cursor-pointer hover:border-blue-300"
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(c.key)}
                      onChange={() => toggle(c.key)}
                      className="mt-0.5 h-4 w-4"
                      aria-label={c.name}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-start justify-between mb-1">
                        <span className="font-medium text-sm">{c.name}</span>
                        <span className="text-xs text-gray-500 ml-2">
                          {c.cost_estimate}
                        </span>
                      </div>
                      <p className="text-xs text-gray-600">{c.description}</p>
                      {c.dependencies.length > 0 && (
                        <p className="text-xs text-gray-400 mt-1">
                          Requires: {c.dependencies.join(", ")}
                        </p>
                      )}
                    </div>
                  </label>
                )
              )}
          </div>
        </div>
      ))}
    </div>
  );
}
