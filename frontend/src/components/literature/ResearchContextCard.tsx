"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/Button";
import { logError } from "@/lib/errorReporting";
import { literature, openAccessVersionLabel, type OpenAlexContext } from "@/lib/literature";

const CAVEAT =
  "Context only. Citation counts do not establish validity or method reuse, and no retraction flag does not guarantee a clean record.";

function retractionLabel(isRetracted: boolean | null): string {
  if (isRetracted === true) return "Retracted, according to OpenAlex";
  if (isRetracted === false) return "No retraction known to OpenAlex";
  return "Retraction status unknown";
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

/**
 * Citation and retraction context from OpenAlex, its open-access copies, and two
 * citation searches. `context` is loaded by the page; `failed` means it could not be.
 */
export function ResearchContextCard({
  context,
  failed,
  canSearch,
}: {
  context: OpenAlexContext | null;
  failed: boolean;
  canSearch: boolean;
}) {
  const router = useRouter();
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  async function citationSearch(query: string) {
    setSearching(true);
    setSearchError(null);
    try {
      await literature.submitSearch({ query, sources: ["openalex"] });
      router.push("/lab-knowledge/literature/searches");
    } catch (e) {
      logError(`starting citation search ${query}`, e);
      setSearchError("The search could not be started. Try again later.");
      setSearching(false);
    }
  }

  let body: React.ReactNode;
  if (failed) {
    body = <p className="text-sm text-gray-600">Research context is unavailable right now.</p>;
  } else if (!context) {
    body = <p className="text-sm text-gray-500">Loading...</p>;
  } else if (!context.available) {
    body = <p className="text-sm text-gray-600">OpenAlex has no record matching this paper.</p>;
  } else {
    const maxYear = Math.max(1, ...context.counts_by_year.map((c) => c.cited_by_count));
    body = (
      <div className="text-sm space-y-3">
        <p className={context.is_retracted ? "font-semibold text-red-700" : "text-gray-700"}>
          {retractionLabel(context.is_retracted)}
        </p>
        <p>
          {context.cited_by_count === null ? "Citation count unknown" : `Cited by ${context.cited_by_count}`}
        </p>
        {context.counts_by_year.length > 0 && (
          <div>
            <p className="text-gray-500 mb-1">Available citation history</p>
            <ul className="space-y-0.5">
              {context.counts_by_year.map((c) => (
                <li
                  key={c.year}
                  aria-label={`${c.year}: ${c.cited_by_count} citations`}
                  className="flex items-center gap-2 text-xs"
                >
                  <span className="w-10 text-gray-500">{c.year}</span>
                  <span
                    className="h-2 rounded bg-bioaf-600"
                    style={{ width: `${(c.cited_by_count / maxYear) * 100}%`, minWidth: "2px" }}
                  />
                  <span className="text-gray-600">{c.cited_by_count}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {context.oa_locations.length > 0 && (
          <div>
            <p className="text-gray-500 mb-1">Open-access copies</p>
            <ul className="space-y-1">
              {context.oa_locations.map((loc) => {
                const url = (loc.landing_page_url ?? loc.pdf_url) as string;
                return (
                  <li key={url}>
                    <a href={url} target="_blank" rel="noreferrer" className="text-bioaf-700 hover:underline">
                      {loc.host ?? hostOf(url)}
                    </a>
                    <div className="text-xs text-gray-500">
                      {openAccessVersionLabel(loc.version)}, license {loc.license ?? "not stated"}
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        {canSearch && context.openalex_id && (
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={searching}
              onClick={() => citationSearch(`cited_by:${context.openalex_id}`)}
            >
              Papers this builds on
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={searching}
              onClick={() => citationSearch(`cites:${context.openalex_id}`)}
            >
              Papers citing this
            </Button>
          </div>
        )}
        {searchError && <p className="text-xs text-red-700">{searchError}</p>}
        <p className="text-xs text-gray-500">{CAVEAT}</p>
      </div>
    );
  }

  return (
    <div className="bg-white rounded shadow p-4">
      <h3 className="font-semibold mb-2">Research context</h3>
      {body}
    </div>
  );
}
