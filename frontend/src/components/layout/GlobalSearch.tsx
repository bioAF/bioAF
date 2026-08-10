"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useDismissOnEscape } from "@/hooks/useDismissOnEscape";
import { QuickSearchHit, searchHitHref, searchHitTypeLabel } from "@/lib/searchLinks";

// Default pause before searching. The search fires once the user stops typing
// for this long, so they can finish a query before results are generated.
const DEFAULT_DEBOUNCE_MS = 1000;

export function GlobalSearch({ debounceMs = DEFAULT_DEBOUNCE_MS }: { debounceMs?: number }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<QuickSearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  // Only meaningful below md, where the field is behind a button. At md and above the
  // field is always laid out, and this state is never read.
  const [expanded, setExpanded] = useState(false);
  const debounced = useDebouncedValue(query, debounceMs);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const collapse = useCallback(() => {
    setExpanded(false);
    setOpen(false);
  }, []);

  // Opening the field and then having to click it to type would be two gestures for
  // one intent.
  useEffect(() => {
    if (expanded) inputRef.current?.focus();
  }, [expanded]);

  useDismissOnEscape(expanded, collapse);

  useEffect(() => {
    const q = debounced.trim();
    if (!q) {
      setResults([]);
      setSearched(false);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .get<{ results: QuickSearchHit[] }>(`/api/search/quick?q=${encodeURIComponent(q)}`)
      .then((data) => {
        if (cancelled) return;
        setResults(data.results);
        setSearched(true);
        setOpen(true);
      })
      .catch(() => {
        if (cancelled) return;
        setResults([]);
        setSearched(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [debounced]);

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        // The expanded field covers the header, so leaving it open after a click
        // elsewhere would hide the controls the user was reaching for.
        setExpanded(false);
      }
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const goTo = (hit: QuickSearchHit) => {
    setQuery("");
    setResults([]);
    setSearched(false);
    setOpen(false);
    setExpanded(false);
    router.push(searchHitHref(hit));
  };

  // Enter on a non-empty query opens the full search results page (`/search`),
  // instead of forcing the user to pick one of the dropdown hits. The term is
  // left in the box; the search page pre-fills from it.
  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "Enter") return;
    const q = query.trim();
    if (!q) return;
    setOpen(false);
    setExpanded(false);
    router.push(`/search?q=${encodeURIComponent(q)}`);
  };

  return (
    <div className="relative w-full max-w-md" ref={containerRef}>
      {/*
        Below md the search is a button that opens the field, not a field.

        The header row is 375px wide on a phone and already carries the hamburger, the
        theme toggle, quick-create, the assistant, the notification bell, the user's name
        and Logout. The field was `flex-1` among them and lost: measured on the deployed
        app it rendered 26 x 34 px at both 375 and 768 (447px at 1440), which reads as an
        unexplained empty white rectangle rather than as a search box. It was a live text
        input squeezed to 26px, not a deliberate collapsed-to-icon treatment.

        So it becomes one. The control is still present at every width -- nothing is
        removed, it is given a size it can be used at -- and the expanded field is
        `fixed` across the header rather than absolutely positioned inside this 26px
        parent, which would only have reproduced the same width.
      */}
      <button
        type="button"
        onClick={() => setExpanded(true)}
        aria-label="Search"
        aria-expanded={expanded}
        data-testid="global-search-toggle"
        className="md:hidden -ml-2 p-2 rounded-md text-gray-600 hover:bg-gray-100 hover:text-gray-900"
      >
        <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z"
          />
        </svg>
      </button>

      <div
        data-testid="global-search-field"
        className={`${
          expanded ? "fixed inset-x-0 top-0 z-50 flex h-16 items-center gap-2 bg-surface px-4" : "hidden"
        } md:static md:z-auto md:flex md:h-auto md:gap-0 md:bg-transparent md:px-0`}
      >
        <div className="relative flex-1">
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            onFocus={() => {
              if (results.length > 0) setOpen(true);
            }}
            placeholder="Search experiments, samples, runs, files..."
            aria-label="Global search"
            className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-1 focus:ring-bioaf-500"
          />
          {open && debounced.trim() && (
            <div className="absolute left-0 right-0 mt-1 bg-white rounded-md shadow-lg border border-gray-200 z-50 max-h-96 overflow-y-auto">
              {loading ? (
                <div className="px-3 py-2 text-sm text-gray-500">Searching...</div>
              ) : results.length === 0 && searched ? (
                <div className="px-3 py-2 text-sm text-gray-500">No matches</div>
              ) : (
                results.map((hit) => (
                  <button
                    key={`${hit.entity_type}-${hit.entity_id}`}
                    onClick={() => goTo(hit)}
                    className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-gray-50"
                  >
                    <span className="text-[10px] uppercase tracking-wide bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded flex-shrink-0">
                      {searchHitTypeLabel(hit.entity_type)}
                    </span>
                    <span className="text-sm text-gray-900 truncate">{hit.name}</span>
                  </button>
                ))
              )}
            </div>
          )}
        </div>

        <button
          type="button"
          onClick={collapse}
          aria-label="Close search"
          className="md:hidden shrink-0 p-2 rounded-md text-gray-600 hover:bg-gray-100 hover:text-gray-900"
        >
          <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>
  );
}
