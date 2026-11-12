"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useResource } from "@/hooks/useResource";
import { useSubmitGuard } from "@/hooks/useSubmitGuard";
import { useToast } from "@/components/shared/Toast";
import { logError, loadFailureMessage } from "@/lib/errorReporting";
import type {
  ExperimentDetail,
  Sample,
  SampleBulkUpdateRequest,
  SampleCreateRequest,
  SampleUpdateRequest,
} from "@/lib/types";
import { bulkUpdate, newSampleDraft, newSamplePayload, sampleEditDraft, sampleEditPayload } from "./experimentForms";

/**
 * The Samples tab's rows, selection and drafts (add, edit, bulk edit, delete). Owned above the
 * tab panels so drafts and the selection survive a trip to another tab, as they always have; the
 * rows are read while the tab is open, freshly each time it opens. The page remounts this owner
 * for each experiment, so nothing here follows the route to another one.
 */
export function useSampleWorkspace(experimentId: string, active: boolean, onExperimentChanged: () => void) {
  const toast = useToast();
  const rows = useResource<Sample[]>(
    active ? `experiment:${experimentId}:samples` : null,
    (signal) => api.get<Sample[]>(`/api/experiments/${experimentId}/samples`, { signal }),
    {
      // An empty samples table is a statement about the experiment. Say when it is really a
      // statement about the request.
      onError: (e) => {
        logError("loading the samples", e);
        toast.error(loadFailureMessage("Samples"));
      },
    },
  );
  const samples = rows.data ?? [];

  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [viewing, setViewing] = useState<Sample | null>(null);
  const [showCsvUpload, setShowCsvUpload] = useState(false);

  const addGuard = useSubmitGuard();
  const [showAdd, setShowAdd] = useState(false);
  const [addForm, setAddForm] = useState<SampleCreateRequest>({});
  const [addCustomFields, setAddCustomFields] = useState<Record<string, string>>({});
  const [addError, setAddError] = useState("");

  const editGuard = useSubmitGuard();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<SampleUpdateRequest>({});
  const [editCustomFields, setEditCustomFields] = useState<Record<string, string>>({});
  const [editError, setEditError] = useState("");

  const bulkGuard = useSubmitGuard();
  const [showBulk, setShowBulk] = useState(false);
  const [bulkForm, setBulkForm] = useState<SampleUpdateRequest>({});
  const [bulkError, setBulkError] = useState("");

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);

  function toggleAdd(experiment: ExperimentDetail) {
    if (!showAdd) {
      const draft = newSampleDraft(experiment);
      setAddForm(draft.form);
      setAddCustomFields(draft.customFields);
    }
    setShowAdd(!showAdd);
  }

  function closeAdd() {
    setShowAdd(false);
    setAddError("");
  }

  async function add() {
    setAddError("");
    try {
      await api.post(`/api/experiments/${experimentId}/samples`, newSamplePayload(addForm, addCustomFields));
      setAddForm({});
      setAddCustomFields({});
      setShowAdd(false);
      void rows.refresh();
      onExperimentChanged();
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "Failed to save sample");
    }
  }

  function startEdit(sample: Sample) {
    const draft = sampleEditDraft(sample);
    setEditingId(sample.id);
    setEditForm(draft.form);
    setEditCustomFields(draft.customFields);
    setEditError("");
  }

  function closeEdit() {
    setEditingId(null);
    setEditError("");
  }

  async function saveEdit() {
    if (!editingId) return;
    setEditError("");
    try {
      await api.patch(`/api/samples/${editingId}`, sampleEditPayload(editForm, editCustomFields));
      setEditingId(null);
      setEditForm({});
      void rows.refresh();
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Failed to save");
    }
  }

  function openBulk() {
    setShowBulk(true);
    setBulkForm({});
    setBulkError("");
  }

  async function applyBulk() {
    if (selected.size === 0) return;
    setBulkError("");
    const update = bulkUpdate(bulkForm);
    if (!update) {
      setBulkError("Set at least one field to update");
      return;
    }
    try {
      const payload: SampleBulkUpdateRequest = { sample_ids: Array.from(selected), update };
      await api.patch("/api/samples/bulk/update", payload);
      setShowBulk(false);
      setBulkForm({});
      setSelected(new Set());
      void rows.refresh();
    } catch (err) {
      setBulkError(err instanceof Error ? err.message : "Failed to save");
    }
  }

  async function deleteSelected() {
    if (selected.size === 0) return;
    setDeleting(true);
    try {
      await api.post("/api/samples/bulk/delete", { sample_ids: Array.from(selected) });
      setShowDeleteConfirm(false);
      setSelected(new Set());
      void rows.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not delete the selected samples. Nothing was removed.");
    } finally {
      setDeleting(false);
    }
  }

  async function updateQc(sampleId: number, qcStatus: string) {
    try {
      await api.patch(`/api/samples/${sampleId}/qc`, { qc_status: qcStatus });
      void rows.refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not update the QC status.");
    }
  }

  function csvImported() {
    void rows.refresh();
    onExperimentChanged();
  }

  function toggle(sampleId: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(sampleId)) next.delete(sampleId);
      else next.add(sampleId);
      return next;
    });
  }

  function toggleAll() {
    if (selected.size === samples.length) setSelected(new Set());
    else setSelected(new Set(samples.map((s) => s.id)));
  }

  return {
    samples,
    selection: { selected, toggle, toggleAll },
    viewing: { sample: viewing, open: setViewing, close: () => setViewing(null) },
    csv: { open: showCsvUpload, setOpen: setShowCsvUpload, imported: csvImported },
    adding: {
      open: showAdd,
      form: addForm,
      setForm: setAddForm,
      customFields: addCustomFields,
      setCustomFields: setAddCustomFields,
      error: addError,
      busy: addGuard.busy,
      toggle: toggleAdd,
      close: closeAdd,
      submit: () => addGuard.run(add),
    },
    editing: {
      id: editingId,
      form: editForm,
      setForm: setEditForm,
      customFields: editCustomFields,
      setCustomFields: setEditCustomFields,
      error: editError,
      busy: editGuard.busy,
      start: startEdit,
      close: closeEdit,
      submit: () => editGuard.run(saveEdit),
    },
    bulk: {
      open: showBulk,
      form: bulkForm,
      setForm: setBulkForm,
      error: bulkError,
      busy: bulkGuard.busy,
      start: openBulk,
      close: () => setShowBulk(false),
      submit: () => bulkGuard.run(applyBulk),
    },
    deletion: {
      confirming: showDeleteConfirm,
      setConfirming: setShowDeleteConfirm,
      deleting,
      submit: deleteSelected,
    },
    updateQc,
  };
}

export type SampleWorkspace = ReturnType<typeof useSampleWorkspace>;
