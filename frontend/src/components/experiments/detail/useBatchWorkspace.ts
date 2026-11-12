"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useResource } from "@/hooks/useResource";
import { useSubmitGuard } from "@/hooks/useSubmitGuard";
import { useToast } from "@/components/shared/Toast";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import type { SampleBatch, SampleBatchCreateRequest, SequencingBatch } from "@/lib/types";

interface Batches {
  sample: SampleBatch[];
  sequencing: SequencingBatch[];
}

/**
 * The Batches tab's rows and its create-batch draft. The draft is owned above the tab panels so it
 * survives a trip to another tab; the rows are read while the tab is open.
 */
export function useBatchWorkspace(experimentId: string, active: boolean, onExperimentChanged: () => void) {
  const toast = useToast();
  const rows = useResource<Batches>(
    active ? `experiment:${experimentId}:batches` : null,
    async (signal) => {
      const [sample, sequencing] = await Promise.all([
        api.get<SampleBatch[]>(`/api/experiments/${experimentId}/sample-batches`, { signal }),
        api.get<SequencingBatch[]>(`/api/experiments/${experimentId}/sequencing-batches`, { signal }),
      ]);
      return { sample, sequencing };
    },
    {
      onError: (e) => {
        logError("loading the batches", e);
        toast.error(loadFailureMessage("Batches"));
      },
    },
  );

  const guard = useSubmitGuard();
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<SampleBatchCreateRequest>({ name: "" });

  async function add() {
    try {
      await api.post(`/api/experiments/${experimentId}/sample-batches`, form);
      setForm({ name: "" });
      setShowForm(false);
      void rows.refresh();
      onExperimentChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not add the batch.");
    }
  }

  return {
    sampleBatches: rows.data?.sample ?? [],
    sequencingBatches: rows.data?.sequencing ?? [],
    form,
    setForm,
    showForm,
    setShowForm,
    busy: guard.busy,
    submit: () => guard.run(add),
  };
}

export type BatchWorkspace = ReturnType<typeof useBatchWorkspace>;
