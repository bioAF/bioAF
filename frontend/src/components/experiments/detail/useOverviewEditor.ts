"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { ExperimentDetail, ExperimentUpdateRequest } from "@/lib/types";
import {
  overviewDraftFrom,
  overviewPayload,
  withFieldDefault,
  type EditableCustomField,
  type OverviewDraft,
} from "./experimentForms";

/**
 * The overview edit draft. Owned above the tab panels so an unsaved edit survives a trip to
 * another tab; the page remounts its owner for each experiment, so the draft never follows the
 * route to another one. The draft is separate from the fetched record, so a refresh of the record
 * cannot overwrite an edit in progress.
 */
export function useOverviewEditor(experimentId: string, onSaved: () => void) {
  const [draft, setDraft] = useState<OverviewDraft | null>(null);
  const [error, setError] = useState("");

  function start(experiment: ExperimentDetail) {
    setDraft(overviewDraftFrom(experiment));
    setError("");
  }

  function cancel() {
    setDraft(null);
    setError("");
  }

  function setForm(patch: ExperimentUpdateRequest) {
    setDraft((d) => (d ? { ...d, form: { ...d.form, ...patch } } : d));
  }

  function setFieldDefault(fieldName: string, value: string | null, isRequired: boolean | null) {
    setDraft((d) => (d ? { ...d, fieldDefaults: withFieldDefault(d.fieldDefaults, fieldName, value, isRequired) } : d));
  }

  function setCustomFields(update: (prev: EditableCustomField[]) => EditableCustomField[]) {
    setDraft((d) => (d ? { ...d, customFields: update(d.customFields) } : d));
  }

  async function save() {
    if (!draft) return;
    setError("");
    try {
      await api.patch(`/api/experiments/${experimentId}`, overviewPayload(draft));
      setDraft(null);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save experiment");
    }
  }

  return { draft, error, start, cancel, setForm, setFieldDefault, setCustomFields, save };
}

export type OverviewEditor = ReturnType<typeof useOverviewEditor>;
