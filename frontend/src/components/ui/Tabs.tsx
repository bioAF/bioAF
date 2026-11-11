"use client";

import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";

/**
 * Tabs that select a panel on the same page: experiment and reference detail.
 *
 * Not for route navigation (links), filter chips or wizard steps, which have
 * their own semantics.
 *
 * Controlled: the page owns the selected key and decides what to mount. Only
 * the selected panel is rendered, as before, so a tab's body mounts when it
 * is selected and anything it holds itself is reset when another tab is
 * chosen; state the page owns survives.
 *
 * Keyboard: the selected tab is the one Tab stop; Left/Right (wrapping), Home
 * and End move focus between tabs, and Enter or Space selects. Selection is
 * manual because selecting a tab loads its data.
 */
export interface TabIds {
  tab: (key: string) => string;
  panel: (key: string) => string;
}

export function useTabIds(): TabIds {
  const base = useId();
  return {
    tab: (key) => `${base}tab-${key}`,
    panel: (key) => `${base}panel-${key}`,
  };
}

export function TabList<K extends string>({
  label,
  ids,
  tabs,
  selected,
  onSelect,
  className = "mb-6",
}: {
  /** Names the tablist for assistive technology. */
  label: string;
  ids: TabIds;
  tabs: { key: K; label: ReactNode }[];
  selected: K;
  onSelect: (key: K) => void;
  className?: string;
}) {
  const listRef = useRef<HTMLDivElement>(null);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const buttons = Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]') ?? []);
    const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (current === -1) return;
    const last = buttons.length - 1;
    const next =
      e.key === "ArrowRight" ? (current === last ? 0 : current + 1)
      : e.key === "ArrowLeft" ? (current === 0 ? last : current - 1)
      : e.key === "Home" ? 0
      : e.key === "End" ? last
      : null;
    if (next === null) return;
    e.preventDefault();
    buttons[next].focus();
  };

  return (
    <div className={`border-b border-hairline ${className}`.trim()}>
      <div ref={listRef} role="tablist" aria-label={label} onKeyDown={onKeyDown} className="flex -mb-px space-x-8">
        {tabs.map((tab) => {
          const isSelected = tab.key === selected;
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              id={ids.tab(tab.key)}
              aria-selected={isSelected}
              aria-controls={isSelected ? ids.panel(tab.key) : undefined}
              tabIndex={isSelected ? 0 : -1}
              onClick={() => onSelect(tab.key)}
              className={`whitespace-nowrap py-2 px-1 border-b-2 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-bioaf-600 ${
                isSelected
                  ? "border-bioaf-500 text-bioaf-600"
                  : "border-transparent text-ink-subtle hover:text-ink-muted hover:border-gray-300"
              }`}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** The selected tab's panel, labelled by that tab. */
export function TabPanel({ ids, tabKey, children }: { ids: TabIds; tabKey: string; children: ReactNode }) {
  return (
    <div role="tabpanel" id={ids.panel(tabKey)} aria-labelledby={ids.tab(tabKey)} tabIndex={0} className="focus:outline-none">
      {children}
    </div>
  );
}
