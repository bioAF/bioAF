"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { onSessionReset, sessionGeneration } from "@/lib/session";
import type { ControlledVocabularyValue, ControlledVocabularyResponse } from "@/lib/types";

const cache = new Map<string, { data: ControlledVocabularyValue[]; ts: number }>();
const CACHE_TTL = 5 * 60 * 1000; // 5 minutes

// Vocabulary is read with the session's credential; a cached answer ends with the session.
onSessionReset(() => cache.clear());

export function useVocabulary(fieldName: string) {
  const [values, setValues] = useState<ControlledVocabularyValue[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const cached = cache.get(fieldName);
    if (cached && Date.now() - cached.ts < CACHE_TTL) {
      setValues(cached.data);
      setLoading(false);
      return;
    }

    let active = true;
    const generation = sessionGeneration();
    const current = () => active && generation === sessionGeneration();
    (async () => {
      try {
        const data = await api.get<ControlledVocabularyResponse>(
          `/api/vocabularies?field=${encodeURIComponent(fieldName)}&active_only=true`
        );
        if (generation !== sessionGeneration()) return;
        cache.set(fieldName, { data: data.values, ts: Date.now() });
        if (current()) setValues(data.values);
      } catch {
        // ignore - field may not have vocabulary values yet
      } finally {
        if (current()) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [fieldName]);

  return { values, loading };
}
