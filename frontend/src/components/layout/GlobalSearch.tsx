"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
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
  const debounced = useDebouncedValue(query, debounceMs);
  const containerRef = useRef<HTMLDivElement>(null);

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
    router.push(searchHitHref(hit));
  };

  return (
    <div className="relative w-full max-w-md" ref={containerRef}>
      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
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
  );
}
