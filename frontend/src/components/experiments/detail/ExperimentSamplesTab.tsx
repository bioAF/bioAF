"use client";

import { useId } from "react";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { Input, controlClass } from "@/components/ui/Input";
import { Alert } from "@/components/shared/Alert";
import { Modal } from "@/components/shared/Modal";
import { DetailModal } from "@/components/shared/DetailModal";
import { VocabularySelect } from "@/components/shared/VocabularySelect";
import { AssaySelect } from "@/components/shared/AssaySelect";
import { CsvUploadModal } from "@/components/experiments/CsvUploadModal";
import { clickableRow } from "@/lib/a11y";
import { SAMPLE_ASSAY_OPTIONS } from "@/lib/types";
import type { ExperimentDetail } from "@/lib/types";
import type { SampleWorkspace } from "./useSampleWorkspace";

const FIELD_SELECT = controlClass();

export function ExperimentSamplesTab({
  experiment,
  workspace,
}: {
  experiment: ExperimentDetail;
  workspace: SampleWorkspace;
}) {
  const { samples, selection, viewing, adding, editing, bulk, deletion } = workspace;
  const editSampleFormId = useId();
  const addForm = adding.form;
  const bulkForm = bulk.form;
  const editForm = editing.form;

  return (
    <div>
      <div className="flex items-center gap-4 mb-4">
        <Button onClick={() => adding.toggle(experiment)}>Add Sample</Button>
        <button
          onClick={() => workspace.csv.setOpen(true)}
          className="bg-surface border border-gray-300 px-4 py-2 rounded-md text-sm hover:bg-surface-muted"
        >
          Import Samples
        </button>
        {selection.selected.size > 0 && (
          <>
            <button
              onClick={bulk.start}
              className="bg-amber-600 text-white px-4 py-2 rounded-md text-sm hover:bg-amber-700"
            >
              Edit Selected ({selection.selected.size})
            </button>
            <Button variant="danger" onClick={() => deletion.setConfirming(true)}>
              Delete Selected ({selection.selected.size})
            </Button>
          </>
        )}
      </div>

      {adding.open && (
        <form
          aria-label="Add sample"
          onSubmit={(e) => {
            e.preventDefault();
            void adding.submit();
          }}
          className="bg-surface rounded-lg shadow p-4 mb-4"
        >
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <Field label="External Sample ID">{(control) => <Input {...control} value={addForm.external_id ?? ""} onChange={(e) => adding.setForm({ ...addForm, external_id: e.target.value })} />}</Field>
            <Field label="Organism">{(control) => <Input {...control} value={addForm.organism ?? ""} onChange={(e) => adding.setForm({ ...addForm, organism: e.target.value })} />}</Field>
            <Field label="Tissue Type">{(control) => <Input {...control} value={addForm.tissue_type ?? ""} onChange={(e) => adding.setForm({ ...addForm, tissue_type: e.target.value })} />}</Field>
            <Field label="Donor ID">{(control) => <Input {...control} value={addForm.donor_source ?? ""} onChange={(e) => adding.setForm({ ...addForm, donor_source: e.target.value })} />}</Field>
            <Field label="Treatment Condition">{(control) => <Input {...control} value={addForm.treatment_condition ?? ""} onChange={(e) => adding.setForm({ ...addForm, treatment_condition: e.target.value })} />}</Field>
            <Field label="Chemistry Version">{(control) => <Input {...control} placeholder="e.g. NextGEM v3.1" value={addForm.chemistry_version ?? ""} onChange={(e) => adding.setForm({ ...addForm, chemistry_version: e.target.value })} />}</Field>
            <Field label="Cell Count">{(control) => <Input {...control} type="number" min={0} value={addForm.cell_count ?? ""} onChange={(e) => adding.setForm({ ...addForm, cell_count: e.target.value ? Number(e.target.value) : null })} />}</Field>
            <Field label="Viability %">{(control) => <Input {...control} type="number" min={0} max={100} step={0.1} value={addForm.viability_pct ?? ""} onChange={(e) => adding.setForm({ ...addForm, viability_pct: e.target.value ? Number(e.target.value) : null })} />}</Field>
            <Field label="Molecule Type">{(control) => <VocabularySelect {...control} fieldName="molecule_type" value={addForm.molecule_type} onChange={(v) => adding.setForm({ ...addForm, molecule_type: v })} placeholder="Molecule Type..." className={FIELD_SELECT} />}</Field>
            <Field label="Library Prep Method">{(control) => <VocabularySelect {...control} fieldName="library_prep_method" value={addForm.library_prep_method} onChange={(v) => adding.setForm({ ...addForm, library_prep_method: v })} placeholder="Library Prep Method..." className={FIELD_SELECT} />}</Field>
            <Field label="Library Layout">{(control) => <VocabularySelect {...control} fieldName="library_layout" value={addForm.library_layout} onChange={(v) => adding.setForm({ ...addForm, library_layout: v })} placeholder="Library Layout..." className={FIELD_SELECT} />}</Field>
            <Field label="Assay">{(control) => <AssaySelect {...control} value={addForm.assay} onChange={(v) => adding.setForm({ ...addForm, assay: v })} className={FIELD_SELECT} />}</Field>
            <Field label="Sample Batch">{(control) => <Input {...control} value={addForm.sample_batch_code ?? ""} onChange={(e) => adding.setForm({ ...addForm, sample_batch_code: e.target.value || null })} />}</Field>
            <Field label="Sequencing Batch">{(control) => <Input {...control} value={addForm.sequencing_batch_code ?? ""} onChange={(e) => adding.setForm({ ...addForm, sequencing_batch_code: e.target.value || null })} />}</Field>
            {experiment.custom_fields.map((cf) => (
              <Field key={cf.id} label={`${cf.field_name}${cf.is_required ? " *" : ""}`}>
                {(control) => (
                  <Input
                    {...control}
                    value={adding.customFields[cf.field_name] ?? ""}
                    onChange={(e) => adding.setCustomFields((prev) => ({ ...prev, [cf.field_name]: e.target.value }))}
                  />
                )}
              </Field>
            ))}
          </div>
          {adding.error && <Alert tone="error" className="mt-3">{adding.error}</Alert>}
          <div className="flex gap-2 mt-3">
            <Button size="sm" type="submit" busy={adding.busy} busyLabel="Saving...">Save</Button>
            <Button size="sm" variant="secondary" onClick={adding.close}>Cancel</Button>
          </div>
        </form>
      )}

      {bulk.open && (
        <form
          aria-label="Bulk edit samples"
          onSubmit={(e) => {
            e.preventDefault();
            void bulk.submit();
          }}
          className="bg-amber-50 border border-amber-200 rounded-lg shadow p-4 mb-4"
        >
          <h3 className="text-sm font-semibold mb-2">Bulk Edit {selection.selected.size} Sample{selection.selected.size > 1 ? "s" : ""}</h3>
          <p className="text-xs text-ink-subtle mb-3">Only fields you fill in will be updated. Blank fields are left unchanged.</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <Field label="Organism">{(control) => <Input {...control} value={bulkForm.organism ?? ""} onChange={(e) => bulk.setForm({ ...bulkForm, organism: e.target.value || undefined })} />}</Field>
            <Field label="Tissue Type">{(control) => <Input {...control} value={bulkForm.tissue_type ?? ""} onChange={(e) => bulk.setForm({ ...bulkForm, tissue_type: e.target.value || undefined })} />}</Field>
            <Field label="Donor ID">{(control) => <Input {...control} value={bulkForm.donor_source ?? ""} onChange={(e) => bulk.setForm({ ...bulkForm, donor_source: e.target.value || undefined })} />}</Field>
            <Field label="Treatment Condition">{(control) => <Input {...control} value={bulkForm.treatment_condition ?? ""} onChange={(e) => bulk.setForm({ ...bulkForm, treatment_condition: e.target.value || undefined })} />}</Field>
            <Field label="Chemistry Version">{(control) => <Input {...control} value={bulkForm.chemistry_version ?? ""} onChange={(e) => bulk.setForm({ ...bulkForm, chemistry_version: e.target.value || undefined })} />}</Field>
            <Field label="Molecule Type">{(control) => <VocabularySelect {...control} fieldName="molecule_type" value={bulkForm.molecule_type} onChange={(v) => bulk.setForm({ ...bulkForm, molecule_type: v || undefined })} placeholder="Molecule Type..." className={FIELD_SELECT} />}</Field>
            <Field label="Library Prep Method">{(control) => <VocabularySelect {...control} fieldName="library_prep_method" value={bulkForm.library_prep_method} onChange={(v) => bulk.setForm({ ...bulkForm, library_prep_method: v || undefined })} placeholder="Library Prep Method..." className={FIELD_SELECT} />}</Field>
            <Field label="Library Layout">{(control) => <VocabularySelect {...control} fieldName="library_layout" value={bulkForm.library_layout} onChange={(v) => bulk.setForm({ ...bulkForm, library_layout: v || undefined })} placeholder="Library Layout..." className={FIELD_SELECT} />}</Field>
            <Field label="Assay">{(control) => <AssaySelect {...control} value={bulkForm.assay} onChange={(v) => bulk.setForm({ ...bulkForm, assay: v || undefined })} className={FIELD_SELECT} />}</Field>
          </div>
          {bulk.error && <Alert tone="error" className="mt-3">{bulk.error}</Alert>}
          <div className="flex gap-2 mt-3">
            <Button size="sm" type="submit" busy={bulk.busy} busyLabel="Applying...">Apply to Selected</Button>
            <Button size="sm" variant="secondary" onClick={bulk.close}>Cancel</Button>
          </div>
        </form>
      )}

      <div className="bg-surface rounded-lg shadow overflow-x-auto">
        <table className="min-w-full divide-y divide-hairline">
          <thead className="bg-gray-50">
            <tr>
              <th scope="col" className="px-2 py-3 text-center">
                <input aria-label="Select all samples"
                  type="checkbox"
                  checked={samples.length > 0 && selection.selected.size === samples.length}
                  onChange={selection.toggleAll}
                  className="rounded border-gray-300"
                />
              </th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">ID</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Organism</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Tissue</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Molecule</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Treatment</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Library Prep</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Library Layout</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Files</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">QC</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-ink-subtle uppercase">Status</th>
              <th scope="col" className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {samples.map((s) => (
              <tr key={s.id} className={`hover:bg-surface-muted cursor-pointer ${selection.selected.has(s.id) ? "bg-blue-50/50" : ""}`} {...clickableRow(() => viewing.open(s))}>
                <td className="px-2 py-3 text-center" onClick={(e) => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    aria-label={`Select sample ${s.external_id ?? s.id}`}
                    checked={selection.selected.has(s.id)}
                    onChange={() => selection.toggle(s.id)}
                    className="rounded border-gray-300"
                  />
                </td>
                <td className="px-4 py-3 text-sm">
                  {s.external_id || `#${s.id}`}
                  {s.relabeled_from && (
                    <span className="block text-xs text-gray-600">before upgrade: {s.relabeled_from}</span>
                  )}
                </td>
                <td className="px-4 py-3 text-sm">{s.organism || "---"}</td>
                <td className="px-4 py-3 text-sm">{s.tissue_type || "---"}</td>
                <td className="px-4 py-3 text-sm">{s.molecule_type || "---"}</td>
                <td className="px-4 py-3 text-sm">{s.treatment_condition || "---"}</td>
                <td className="px-4 py-3 text-sm">{s.library_prep_method || "---"}</td>
                <td className="px-4 py-3 text-sm">{s.library_layout || "---"}</td>
                <td className="px-4 py-3 text-sm">{s.file_count}</td>
                <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                  <select
                    aria-label={`QC status for sample ${s.external_id ?? s.id}`}
                    value={s.qc_status ?? ""}
                    onChange={(e) => { if (e.target.value) void workspace.updateQc(s.id, e.target.value); }}
                    className="text-xs border rounded px-2 py-1"
                  >
                    <option value="">---</option>
                    <option value="pass">Pass</option>
                    <option value="warning">Warning</option>
                    <option value="fail">Fail</option>
                  </select>
                </td>
                <td className="px-4 py-3 text-sm text-ink-subtle">{s.status.replace(/_/g, " ")}</td>
                <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => editing.start(s)}
                    className="text-xs px-2 py-1 border border-bioaf-600 text-bioaf-600 rounded hover:bg-bioaf-50"
                  >
                    Edit
                  </button>
                </td>
              </tr>
            ))}
            {samples.length === 0 && (
              <tr><td colSpan={10} className="px-4 py-8 text-center text-ink-subtle">No samples yet</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {viewing.sample && (() => {
        const sample = viewing.sample;
        return (
          <DetailModal
            title={sample.external_id || `Sample #${sample.id}`}
            onClose={viewing.close}
            fields={[
              { label: "Internal ID", value: `#${sample.id}` },
              { label: "External ID", value: sample.external_id },
              ...(sample.relabeled_from ? [{ label: "Label before upgrade", value: sample.relabeled_from }] : []),
              { label: "Status", value: sample.status.replace(/_/g, " ") },
              { label: "Organism", value: sample.organism },
              { label: "Tissue Type", value: sample.tissue_type },
              { label: "Molecule Type", value: sample.molecule_type },
              { label: "Assay", value: sample.assay ? (SAMPLE_ASSAY_OPTIONS.find((o) => o.value === sample.assay)?.label ?? sample.assay) : null },
              { label: "Treatment", value: sample.treatment_condition },
              { label: "Library Prep", value: sample.library_prep_method },
              { label: "Library Layout", value: sample.library_layout },
              { label: "Donor ID", value: sample.donor_source },
              { label: "Chemistry Version", value: sample.chemistry_version },
              { label: "Cell Count", value: sample.cell_count?.toLocaleString() },
              { label: "Viability %", value: sample.viability_pct != null ? `${sample.viability_pct}%` : null },
              { label: "Sample Batch", value: sample.sample_batch?.name },
              { label: "Sequencing Batch", value: sample.sequencing_batch?.code },
              { label: "Batch Position", value: sample.sequencing_batch_position },
              { label: "QC Status", value: sample.qc_status },
              { label: "QC Notes", value: sample.qc_notes },
              { label: "Prep Notes", value: sample.prep_notes },
              ...(sample.custom_fields ?? []).map((cf) => ({ label: cf.field_name, value: cf.field_value })),
              { label: "Created", value: new Date(sample.created_at).toLocaleString() },
              { label: "Updated", value: new Date(sample.updated_at).toLocaleString() },
            ]}
            actions={
              <button
                onClick={() => { viewing.close(); editing.start(sample); }}
                className="px-3 py-1.5 border border-bioaf-600 text-bioaf-600 rounded text-sm hover:bg-bioaf-50"
              >
                Edit
              </button>
            }
          />
        );
      })()}

      {editing.id !== null && (
        <Modal
          open
          title="Edit Sample"
          onClose={editing.close}
          size="md"
          footer={
            <>
              <Button variant="secondary" onClick={editing.close}>Cancel</Button>
              <Button type="submit" form={editSampleFormId} busy={editing.busy} busyLabel="Saving...">Save Changes</Button>
            </>
          }
        >
          <form
            id={editSampleFormId}
            onSubmit={(e) => {
              e.preventDefault();
              void editing.submit();
            }}
          >
            <div className="grid grid-cols-2 gap-3">
              <Field label="External Sample ID">{(control) => <Input {...control} value={editForm.external_id ?? ""} onChange={(e) => editing.setForm({ ...editForm, external_id: e.target.value })} />}</Field>
              <Field label="Organism">{(control) => <Input {...control} value={editForm.organism ?? ""} onChange={(e) => editing.setForm({ ...editForm, organism: e.target.value })} />}</Field>
              <Field label="Tissue Type">{(control) => <Input {...control} value={editForm.tissue_type ?? ""} onChange={(e) => editing.setForm({ ...editForm, tissue_type: e.target.value })} />}</Field>
              <Field label="Donor ID">{(control) => <Input {...control} value={editForm.donor_source ?? ""} onChange={(e) => editing.setForm({ ...editForm, donor_source: e.target.value })} />}</Field>
              <Field label="Treatment Condition">{(control) => <Input {...control} value={editForm.treatment_condition ?? ""} onChange={(e) => editing.setForm({ ...editForm, treatment_condition: e.target.value })} />}</Field>
              <Field label="Chemistry Version">{(control) => <Input {...control} value={editForm.chemistry_version ?? ""} onChange={(e) => editing.setForm({ ...editForm, chemistry_version: e.target.value })} />}</Field>
              <Field label="Cell Count">{(control) => <Input {...control} type="number" min={0} value={editForm.cell_count ?? ""} onChange={(e) => editing.setForm({ ...editForm, cell_count: e.target.value ? Number(e.target.value) : null })} />}</Field>
              <Field label="Viability %">{(control) => <Input {...control} type="number" min={0} max={100} step={0.1} value={editForm.viability_pct ?? ""} onChange={(e) => editing.setForm({ ...editForm, viability_pct: e.target.value ? Number(e.target.value) : null })} />}</Field>
              <Field label="Molecule Type">{(control) => <VocabularySelect {...control} fieldName="molecule_type" value={editForm.molecule_type} onChange={(v) => editing.setForm({ ...editForm, molecule_type: v })} placeholder="Molecule Type..." className={FIELD_SELECT} />}</Field>
              <Field label="Library Prep Method">{(control) => <VocabularySelect {...control} fieldName="library_prep_method" value={editForm.library_prep_method} onChange={(v) => editing.setForm({ ...editForm, library_prep_method: v })} placeholder="Library Prep Method..." className={FIELD_SELECT} />}</Field>
              <Field label="Library Layout">{(control) => <VocabularySelect {...control} fieldName="library_layout" value={editForm.library_layout} onChange={(v) => editing.setForm({ ...editForm, library_layout: v })} placeholder="Library Layout..." className={FIELD_SELECT} />}</Field>
              <Field label="Assay">{(control) => <AssaySelect {...control} value={editForm.assay} onChange={(v) => editing.setForm({ ...editForm, assay: v })} className={FIELD_SELECT} />}</Field>
              <Field label="Sample Batch">{(control) => <Input {...control} value={editForm.sample_batch_code ?? ""} onChange={(e) => editing.setForm({ ...editForm, sample_batch_code: e.target.value || null })} />}</Field>
              <Field label="Sequencing Batch">{(control) => <Input {...control} value={editForm.sequencing_batch_code ?? ""} onChange={(e) => editing.setForm({ ...editForm, sequencing_batch_code: e.target.value || null })} />}</Field>
              {experiment.custom_fields.map((cf) => (
                <Field key={cf.id} label={`${cf.field_name}${cf.is_required ? " *" : ""}`}>
                  {(control) => (
                    <Input
                      {...control}
                      value={editing.customFields[cf.field_name] ?? ""}
                      onChange={(e) => editing.setCustomFields((prev) => ({ ...prev, [cf.field_name]: e.target.value }))}
                    />
                  )}
                </Field>
              ))}
            </div>
            {editing.error && <Alert tone="error" className="mt-3">{editing.error}</Alert>}
          </form>
        </Modal>
      )}
    </div>
  );
}

/** The samples dialogs the page keeps outside the tab panel (import, delete confirmation). */
export function ExperimentSampleDialogs({
  experiment,
  workspace,
}: {
  experiment: ExperimentDetail;
  workspace: SampleWorkspace;
}) {
  const { csv, deletion, selection } = workspace;
  const count = selection.selected.size;
  return (
    <>
      {csv.open && (
        <CsvUploadModal
          experimentId={experiment.id}
          existingCustomFields={experiment.custom_fields?.map((cf) => cf.field_name) ?? []}
          onClose={() => csv.setOpen(false)}
          onSuccess={csv.imported}
        />
      )}
      <Modal
        open={deletion.confirming}
        title="Delete Samples"
        onClose={() => deletion.setConfirming(false)}
        size="sm"
        footer={
          <>
            <button
              onClick={() => deletion.setConfirming(false)}
              disabled={deletion.deleting}
              className="border px-4 py-2 rounded-md text-sm"
            >
              Cancel
            </button>
            <Button variant="danger" onClick={() => void deletion.submit()} busy={deletion.deleting} busyLabel="Deleting...">
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-gray-600 mb-1">
          You are about to delete <span className="font-semibold">{count}</span> sample{count > 1 ? "s" : ""}.
        </p>
        <p className="text-sm text-red-600 mb-4">
          This action cannot be undone. File links and pending auto-runs for these samples will be removed. Existing pipeline runs will be kept for audit purposes.
        </p>
      </Modal>
    </>
  );
}
